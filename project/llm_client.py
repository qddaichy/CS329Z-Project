"""Provider selection and Tinker Messages-to-ChatCompletion adaptation.

Tinker documents base-model names and tool round trips on its Anthropic endpoint.
Keep native requests/responses auditable; the agent's API allowlist and validators
remain responsible for deciding which returned calls may execute.
"""
import copy
import json
import time
from types import SimpleNamespace
import uuid

import httpx
from openai.types.chat import ChatCompletion


TINKER_BASE_URL = 'https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api'
ADAPTER_VERSION = 4


def create_client(config, key, timeout):
    provider = config.get('provider', 'openai')
    if provider == 'gemini':
        return GeminiClient(key, base_url=config['base_url'], timeout=timeout,
                            thinking=True, reasoning_effort=config.get('reasoning_effort', 'low'),
                            max_attempts=config.get('max_attempts', 1))
    if provider == 'tinker':
        api_style = config.get('api_style', 'anthropic')
        if api_style not in {'anthropic', 'openai'}:
            raise ValueError(f'Unsupported Tinker API style: {api_style}')
        client_type = TinkerOpenAIClient if api_style == 'openai' else TinkerClient
        return client_type(key, base_url=config.get('base_url') or TINKER_BASE_URL,
                            timeout=timeout, thinking=config.get('thinking', False),
                            reasoning_effort=config.get('reasoning_effort', 'low'),
                            max_attempts=config.get('max_attempts', 1))
    if provider not in {'openai', 'openrouter'}:
        raise ValueError(f'Unsupported LLM provider: {provider}')
    import os
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=config.get('base_url') or os.getenv('OPENAI_BASE_URL'),
                  timeout=timeout, max_retries=0)


def convert_messages(messages):
    """Preserve call IDs, JSON arguments and tool-result ordering across turns."""
    system, conversation = [], []
    for message in messages:
        role = message['role']
        content = message.get('content') or ''
        if not isinstance(content, str):
            raise ValueError('The state-only Tinker adapter accepts text messages only.')
        if role == 'system':
            system.append(content)
            continue
        blocks = []
        if role == 'tool':
            role = 'user'
            blocks.append({'type': 'tool_result', 'tool_use_id': message['tool_call_id'], 'content': content})
        elif role in {'user', 'assistant'}:
            if content:
                blocks.append({'type': 'text', 'text': content})
            for call in message.get('tool_calls') or []:
                arguments = json.loads(call['function']['arguments'])
                if role != 'assistant' or not isinstance(arguments, dict):
                    raise ValueError('Invalid historical tool call.')
                blocks.append({'type': 'tool_use', 'id': call['id'],
                               'name': call['function']['name'], 'input': arguments})
        else:
            raise ValueError(f'Unsupported conversation role: {role}')
        if not blocks:
            raise ValueError('Empty conversation message.')
        if conversation and conversation[-1]['role'] == role:
            conversation[-1]['content'].extend(blocks)
        else:
            conversation.append({'role': role, 'content': blocks})
    return '\n\n'.join(system), conversation


class TinkerClient:
    def __init__(self, api_key, *, base_url=TINKER_BASE_URL, timeout=45,
                 thinking=False, reasoning_effort='low', max_attempts=1, transport=None):
        if type(thinking) is not bool:
            raise ValueError('Tinker thinking must be true or false.')
        self.base_url = base_url.rstrip('/')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError('Tinker max_attempts must be 1 to 3.')
        self.max_attempts = max_attempts
        self.thinking = thinking
        if reasoning_effort not in {'low', 'medium', 'xhigh'}:
            raise ValueError('Tinker effort must be low, medium or xhigh.')
        self.reasoning_effort = reasoning_effort
        self.trace = None
        self.request_count = 0
        self.usage_incomplete_count = 0
        self.http = httpx.Client(timeout=timeout, transport=transport, headers={
            'x-api-key': api_key, 'anthropic-version': '2023-06-01'})
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        # Supervised archiving wraps both entry points, even for chat-only models.
        self.responses = SimpleNamespace(create=self.unsupported_responses)

    @staticmethod
    def unsupported_responses(**kwargs):
        raise ValueError('Tinker uses its Messages endpoint; choose a supported Tinker model, not a Terra model ID.')

    def _trace(self, event):
        if self.trace is not None:
            self.trace(copy.deepcopy(event))

    def create(self, *, model, messages, tools, tool_choice='required',
               parallel_tool_calls=False, temperature=0, max_completion_tokens=2048):
        if model in {'gpt-6.1-terra', 'openai/gpt-6.1-terra'}:
            raise ValueError('Terra is not a Tinker model.')
        system, conversation = convert_messages(messages)
        if tool_choice not in {'required', 'auto', 'none'}:
            raise ValueError('Unsupported Tinker tool choice.')
        choice = {'type': {'required': 'any', 'auto': 'auto', 'none': 'none'}[tool_choice]}
        if tool_choice != 'none':
            choice['disable_parallel_tool_use'] = not parallel_tool_calls
        payload = {'model': model, 'system': system, 'messages': conversation,
                   'max_tokens': max_completion_tokens, 'temperature': temperature,
                   'thinking': {'type': 'enabled' if self.thinking else 'disabled'},
                   'tools': [{'name': t['function']['name'],
                              'description': t['function'].get('description', ''),
                              'input_schema': copy.deepcopy(t['function']['parameters'])} for t in tools],
                   'tool_choice': choice}
        if self.thinking:
            payload.pop('thinking')
            payload['output_config'] = {'effort': self.reasoning_effort}
        elif model.startswith('Qwen/Qwen3.8-'):
            # Live endpoint rejects disabled->"none", while output_config rejects
            # boolean false. Do not silently enable reasoning for the caller.
            raise ValueError('Qwen3.8 currently requires thinking=true on this endpoint; use reasoning_effort=low.')
        data = self._request(payload)
        calls, text = [], []
        for block in data['content']:
            if block['type'] == 'tool_use':
                if not isinstance(block['input'], dict):
                    raise ValueError('Tinker returned non-object tool arguments.')
                calls.append({'id': block['id'], 'type': 'function', 'function': {
                    'name': block['name'], 'arguments': json.dumps(block['input'], ensure_ascii=False)}})
            elif block['type'] == 'text':
                text.append(block['text'])
        usage = data.get('usage') or {}
        # Missing usage must never become an apparently zero-cost measured run.
        if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')):
            raise ValueError('Tinker did not return valid input/output token usage.')
        cache = {key: usage.get(key) or 0 for key in ('cache_creation_input_tokens', 'cache_read_input_tokens')}
        if not all(type(value) is int and value >= 0 for value in cache.values()):
            raise ValueError('Tinker returned invalid cache token usage.')
        prompt_tokens = usage['input_tokens'] + sum(cache.values())
        self.usage_incomplete_count -= 1
        reason = 'length' if data.get('stop_reason') == 'max_tokens' else 'tool_calls' if calls else 'stop'
        if reason == 'length':
            calls = []  # Never execute a tool from a truncated provider response.
        return ChatCompletion.model_validate({
            'id': data['id'], 'object': 'chat.completion', 'created': int(time.time()),
            'model': data.get('model', model), 'choices': [{'index': 0, 'finish_reason': reason,
                'message': {'role': 'assistant', 'content': '\n'.join(text) or None, 'tool_calls': calls}}],
            'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': usage['output_tokens'],
                      'total_tokens': prompt_tokens + usage['output_tokens'],
                      'prompt_tokens_details': {'cached_tokens': cache['cache_read_input_tokens']}},
            'provider': 'tinker', 'provider_stop_reason': data.get('stop_reason')})

    def _request(self, payload, path='/v1/messages'):
        for attempt in range(1, self.max_attempts + 1):
            request_id = uuid.uuid4().hex
            self._trace({'type': 'request', 'id': request_id, 'attempt': attempt,
                         'endpoint': self.base_url + path, 'payload': payload})
            self.request_count += 1
            self.usage_incomplete_count += 1
            try:
                response = self.http.post(self.base_url + path, json=payload)
                try:
                    data = response.json()
                except ValueError:
                    data = {'unparsed_body': response.text}
                self._trace({'type': 'response', 'id': request_id, 'status_code': response.status_code, 'payload': data})
                response.raise_for_status()
            except Exception as error:
                retryable = isinstance(error, httpx.TransportError) or (
                    isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {502,503,504})
                retry = retryable and attempt < self.max_attempts
                self._trace({'type': 'request_error', 'id': request_id, 'error': str(error), 'retry': retry})
                if not retry:
                    raise
                time.sleep(2 ** attempt)
            else:
                return data

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TinkerOpenAIClient(TinkerClient):
    """OpenAI-compatible endpoint with an explicit non-thinking Kimi setting."""
    def __init__(self, api_key, **kwargs):
        super().__init__(api_key, **kwargs)
        self.http.headers.pop('x-api-key', None)
        self.http.headers.pop('anthropic-version', None)
        self.http.headers['authorization'] = 'Bearer ' + api_key

    def create(self, *, model, messages, tools, tool_choice='required',
               parallel_tool_calls=False, temperature=0, max_completion_tokens=2048):
        if self.thinking or not model.startswith('moonshotai/Kimi-K2.6'):
            raise ValueError('This OpenAI-compatible profile supports Kimi-K2.6 with thinking disabled only.')
        payload = {'model': model, 'messages': copy.deepcopy(messages), 'tools': copy.deepcopy(tools),
                   'tool_choice': tool_choice, 'parallel_tool_calls': parallel_tool_calls,
                   'temperature': temperature, 'max_tokens': max_completion_tokens,
                   'reasoning_effort': False}
        data = self._request(payload, path='/chat/completions')
        usage = data.get('usage') or {}
        if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens','completion_tokens','total_tokens')):
            raise ValueError('Tinker did not return valid token usage.')
        if usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']:
            raise ValueError('Tinker returned inconsistent token usage.')
        choices = data.get('choices') or []
        if len(choices) != 1:
            raise ValueError('Tinker must return exactly one choice.')
        message = choices[0].get('message') or {}
        if message.get('reasoning_content'):
            raise ValueError('Provider returned reasoning content despite thinking being disabled.')
        if choices[0].get('finish_reason') == 'length':
            message['tool_calls'] = []
        result = ChatCompletion.model_validate(data)
        self.usage_incomplete_count -= 1
        return result


class GeminiClient(TinkerOpenAIClient):
    """Gemini OpenAI-compatible chat with complete function-call signatures."""
    def __init__(self, api_key, *, reasoning_effort='low', **kwargs):
        if reasoning_effort not in {'minimal', 'low', 'medium', 'high'}:
            raise ValueError('Gemini reasoning_effort must be minimal, low, medium or high.')
        # The shared Tinker constructor has its own provider-specific choices.
        super().__init__(api_key, reasoning_effort='low', **kwargs)
        self.reasoning_effort = reasoning_effort

    def create(self, *, model, messages, tools, tool_choice='required',
               parallel_tool_calls=False, temperature=0, max_completion_tokens=2048):
        if not model.startswith('gemini-'):
            raise ValueError('Choose a Gemini model for the Gemini provider.')
        if model.startswith('gemini-3.8-flash') and self.reasoning_effort == 'minimal':
            raise ValueError('Gemini 3.8 Flash does not support minimal; choose low, medium or high.')
        history = copy.deepcopy(messages)
        names = {}
        for message in history:
            if message.get('role') == 'assistant':
                for call in message.get('tool_calls') or []:
                    names[call['id']] = call['function']['name']
            elif message.get('role') == 'tool':
                if message.get('tool_call_id') not in names:
                    raise ValueError('A Gemini tool result has no preceding function call in the retained history.')
                message['name'] = names[message['tool_call_id']]
        payload = {'model': model, 'messages': history, 'tools': copy.deepcopy(tools),
                   'tool_choice': tool_choice, 'parallel_tool_calls': parallel_tool_calls,
                   'max_tokens': max_completion_tokens, 'reasoning_effort': self.reasoning_effort}
        data = self._request(payload, path='/chat/completions')
        usage = data.get('usage') or {}
        if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens','completion_tokens','total_tokens')):
            raise ValueError('Gemini did not return valid token usage.')
        other_tokens = usage['total_tokens'] - usage['prompt_tokens'] - usage['completion_tokens']
        if other_tokens < 0:
            raise ValueError('Gemini returned inconsistent token usage; inspect native response.')
        # Gemini's compatibility endpoint may exclude thinking tokens from its
        # completion count while including them in total_tokens. Preserve the
        # native response in provider_io and ensure episode totals include them.
        usage['provider_visible_completion_tokens'] = usage['completion_tokens']
        usage['provider_other_tokens'] = other_tokens
        usage['completion_tokens'] += other_tokens
        choices = data.get('choices') or []
        if len(choices) != 1:
            raise ValueError('Gemini must return exactly one choice.')
        message = choices[0].get('message') or {}
        if choices[0].get('finish_reason') == 'length':
            message['tool_calls'] = []
        elif message.get('tool_calls'):
            sig = (message['tool_calls'][0].get('extra_content') or {}).get('google', {}).get('thought_signature')
            if not isinstance(sig, str) or not sig:
                raise ValueError('Gemini function call is missing its required thought signature.')
        result = ChatCompletion.model_validate(data)
        self.usage_incomplete_count -= 1
        return result
