"""Existing StarDojo API wrappers and transport, extracted from upstream env/actions.py."""

import socket
import json
import os
import time
import struct
import mmap
import dotenv
import platform
import cbor

from project.paths import DATA_ROOT, ENV_FILE

dotenv.load_dotenv(ENV_FILE)

crafting_recipes_path = DATA_ROOT / 'CraftingRecipes.json'
with crafting_recipes_path.open(encoding='utf-8') as recipes_file:
    _crafting_recipes = json.load(recipes_file)
mmap_size = 4 * 1024 * 1024  # 8MB



class CaseInsensitiveDict(dict):
    def __setitem__(self, key, value):
        super().__setitem__(key.lower(), value)

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def __delitem__(self, key):
        return super().__delitem__(key.lower())

    def __contains__(self, key):
        return super().__contains__(key.lower())

    def get(self, key, default=None):
        return super().get(key.lower(), default)

    def pop(self, key, default=None):
        return super().pop(key.lower(), default)

    def setdefault(self, key, default=None):
        return super().setdefault(key.lower(), default)


def convert_to_case_insensitive_dict(d):
    ci_dict = CaseInsensitiveDict()
    for key, value in d.items():
        if isinstance(value, dict):
            value = convert_to_case_insensitive_dict(value)

        if isinstance(value, list):
            value_new = []
            for list_item in value:
                if isinstance(list_item, dict):
                    value_new.append(convert_to_case_insensitive_dict(list_item))
                else:
                    value_new.append(list_item)
            value = value_new
        ci_dict[key] = value
    return ci_dict


class SharedMemoryReader:
    def __init__(self, mmap_file, port):
        self.mmap_size = mmap_size

        os_type = platform.system()
        stardew_app_path = os.getenv("STARDEW_APP_PATH")
        stardew_app_parent_path = os.path.dirname(stardew_app_path)
        if os_type == "Linux":
            mmap_file = os.path.join(f"{stardew_app_parent_path}/shared_memory_{port}.bin")
        elif os_type == "Darwin":
            mmap_file = os.path.join(f"{stardew_app_parent_path}/shared_memory_{port}.bin")
        else:
            mmap_file = os.path.join(f"{stardew_app_parent_path}\shared_memory_{port}.bin")
        self.mmap_file = mmap_file
        self.f = open(self.mmap_file, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), self.mmap_size, access=mmap.ACCESS_WRITE)

    def read_from_mmap(self):
        start_time = time.time()
        while True:
            self.mm.seek(0) 
            flag = struct.unpack("B", self.mm.read(1))[0]
            if time.time() - start_time > 30:
                print("Timeout: Server is not ready.")
                return None
            if flag == 1:
                self.mm.seek(4)
                length = struct.unpack("I", self.mm.read(4))[0] 
                if length > 0:
                    data = self.mm.read(length)
                    print(type(data))
                    data = cbor.loads(data)
                    data = convert_to_case_insensitive_dict(data)
                    self.mm.seek(0)
                    self.mm.write(struct.pack("B", 0))
                    return data

    def close(self):
        self.mm.close()
        self.f.close()


class ActionProxy:
    def __init__(self, port: int):
        self.port = port
        self.timeout = 5
        self.mmap_reader = None

    def set_mmap_reader(self):
        self.mmap_reader = SharedMemoryReader(mmap_size, self.port)

    def _post_message(self, message: str, print_message: bool = True) -> str:

        client_socket = None
        response = bytearray()
        start_time = time.time()
        try:
            host = '127.0.0.1'
            port = self.port
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            
            client_socket.setblocking(False)

            if f"move" in message:
                client_socket.settimeout(30)
            elif "observe" in message or "get_surroundings" in message:
                client_socket.settimeout(30)
            elif "wait_for_server" in message:
                client_socket.settimeout(30)
            else:
                client_socket.settimeout(self.timeout)

           
            result = client_socket.connect_ex((host, port))  
            reconnect_time = 0
            while result != 0:
                assert reconnect_time <=10, "reconnect too many time, the game need restart!"
                print(message.encode('utf-8'), "reconnecting!", result)
                result = client_socket.connect_ex((host, port)) 
                reconnect_time += 1
          
            client_socket.sendall(message.encode('utf-8'))
            is_observe = message.split("%", 1)[0] == "observe"

            if is_observe:
                # print("using mmap to receive message")
                return self.mmap_reader.read_from_mmap()

            while True:
                data = client_socket.recv(65536)
                if not data:
                    raise ConnectionError("Server closed the connection before <EOF>.")
                response.extend(data)
                if response.endswith(b"<EOF>"):
                    break

            full_response = response[:-5].decode('utf-8')
            # if print_message:
                # print(f"response from server(sample)：{full_response[:200]}")
            return full_response

        except AssertionError as e:
            
            print(f"AssertionError: {e}")
            assert False, f"got a Error: {e}"

        except Exception as e:
            print(message.encode('utf-8'))
            print(f"Error: {e}")
            if print_message:
                full_response = response[:80].decode('utf-8', errors='replace')
                print(f"response from server(sample)：{full_response[:20]}")


        finally:
            if client_socket:
                client_socket.close()
            end_time = time.time()
            # print("running time for function ", message, " is ", end_time - start_time)

    def wait_for_server(self):
        start_time = time.time()
        host = '127.0.0.1'
        port = self.port
        while True:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                    client_socket.connect((host, port))
                print("Server is ready and listening.")
                break

            except ConnectionRefusedError:
                if time.time() - start_time > 30:
                    print("Timeout: Server is not ready.")
                    break
                print("Waiting for server to start listening...")
                time.sleep(1)  

    def move(self, x: int, y: int) -> bool:
        message = f"move_relative%{x}%{y}"
        success = self._post_message(message)
        return isinstance(success, str) and success.strip().lower() == "true"

    def move_step(self, direction: int) -> bool:
        if (direction < 1) or (direction > 4):
            raise ValueError("Direction must be between 1 and 4")
        message = f"move_step%{direction}"
        reply = self._post_message(message)
        return isinstance(reply, str) and reply.strip().lower() == "true"

    def craft(self, item_id: int) -> None:
        '''
        ### Usage
        Craft an item based on its item ID.

        ### Paramaters
        item_id: All possible items to be crafted
        '''
        crafting_dict = _crafting_recipes["content"]
        crafting_id = list(crafting_dict.keys())[item_id]
        message = f"craft%{crafting_id}"
        self._post_message(message)

    def turn(self, direction: int) -> None:
        if direction<0 or direction>3:
            print("invalid direction for turning")
            return
        message = f"turn%{direction}"
        self._post_message(message)

    def open_map(self) -> None:
        message = f"open_map"
        self._post_message(message)

    def exit_menu(self) -> None:
        message = f"exit_menu"
        self._post_message(message)
        
    def use(self, direction: int) -> None:
        '''
        ### Usage
        Use an item from the inventory, specifying the direction of use.

        ### Paramaters
        slot_index: Inventory slot indices
        direction: 0: up, 1: right, 2: down, 3: left
        '''
        self.turn(direction)
        message = f"use"
        self._post_message(message)

    def choose_item(self, slot_index: int) -> None:
        message = f"choose_item%{slot_index}"
        self._post_message(message)

    # def interact(self) -> None:
    #     message = f"interact"
    #     self._post_message(message)
        
    def interact(self, direction: int) -> None:
        '''
        ### Usage
        Use an item from the inventory, specifying the direction of use.

        ### Paramaters
        slot_index: Inventory slot indices
        direction: 0: up, 1: right, 2: down, 3: left
        '''
        self.turn(direction)
        message = f"interact"
        self._post_message(message)
        
    def choose_option(self, option_index: int, quantity: int = None, direction: int = None) -> None:
        if quantity is None:
            quantity = 0
        if direction is None:
            direction = 0
        message = f"choose_option%{option_index}%{quantity}%{direction}"
        self._post_message(message)

    def sell_current_item(self):
        message = "sell_current_item"
        self._post_message(message)

    def take_from_chest(self, slot_index: int, quantity: int):
        self._post_message(f"take_from_chest%{slot_index}%{quantity}")

    def put_to_chest(self, slot_index: int, quantity: int):
        self._post_message(f"put_to_chest%{slot_index}%{quantity}")

    def attach_item(self, slot_index: int) -> None:
        message = f"attach%{slot_index}"
        self._post_message(message)

    def observe(self) -> str:
        message = "observe_v2%3"
        ret_str = self._post_message(message)
        return ret_str

    def observe_state(self, size: int = 3) -> str:
        """Fetch player and nearby tile state without screenshot data."""
        if not isinstance(size, int) or not 0 <= size <= 64:
            raise ValueError("Observation radius must be an integer between 0 and 64.")
        return self._post_message(f"observe_v2%state:{size}")

    def observe_menu(self) -> str:
        """Read the active menu, including supported dialogue/shop/chest details."""
        return self._post_message("observe_v2%menu")

    def observe_world_map(self) -> str:
        """Read loaded locations and warp connections without opening the map UI."""
        return self._post_message("observe_v2%world")

    def observe_crops(self) -> str:
        """Read tilled soil and crop status for the current location."""
        return self._post_message("observe_v2%crops")

    def observe_economy(self) -> str:
        """Read inventory details and the pending shipping bin on the farm."""
        return self._post_message("observe_v2%economy")

    def observe_machines(self) -> str:
        return self._post_message("observe_v2%machines")

    def observe_recipes(self) -> str:
        return self._post_message("observe_v2%recipes")

    def observe_quests(self) -> str:
        return self._post_message("observe_v2%quests")

    def open_quests(self) -> str:
        return self._post_message("observe_v2%open_quests")

    def quest_menu(self, action: int, index: int) -> str:
        return self._post_message(f"observe_v2%quest_menu:{action}:{index}")

    def request_sleep(self) -> str:
        """Open the normal sleep confirmation while standing on the player's bed."""
        return self._post_message("observe_v2%request_sleep")

    def confirm_sleep(self) -> str:
        return self._post_message("observe_v2%confirm_sleep")

    def level_up_menu(self, choice: int) -> str:
        return self._post_message(f"observe_v2%level_up_menu:{choice}")

    def observe_npcs(self) -> str:
        return self._post_message("observe_v2%npcs")

    def observe_farm(self) -> str:
        return self._post_message("observe_v2%farm")

    def observe_location_details(self) -> str:
        return self._post_message("observe_v2%location_details")

    def observe_entities(self) -> str:
        return self._post_message("observe_v2%entities")

    def observe_fishing(self) -> str:
        return self._post_message("observe_v2%fishing")

    def fish_input(self, pressed: int, duration_ms: int) -> str:
        """Hold or release the fishing button briefly, only inside the minigame."""
        return self._post_message(f"observe_v2%fish_input:{pressed}:{duration_ms}")

    def fish_hook(self) -> str:
        return self._post_message("observe_v2%fish_hook")

    def fish_collect(self) -> str:
        return self._post_message("observe_v2%fish_collect")

    def animal_menu(self, action: int, index: int) -> str:
        return self._post_message(f"observe_v2%animal_menu:{action}:{index}")

    def animal_pan(self, dx: int, dy: int) -> str:
        return self._post_message(f"observe_v2%animal_pan:{dx}:{dy}")

    def pet_animal(self, direction: int) -> str:
        return self._post_message(f"observe_v2%pet_animal:{direction}")

    def carpenter(self, action: int, x: int, y: int) -> str:
        return self._post_message(f"observe_v2%carpenter:{action}:{x}:{y}")

    def unattach_item(self) -> None:
        message = "unattach"
        self._post_message(message)

    def enter_load_menu(self) -> None:
        message = f"enter_load_menu"
        self._post_message(message)

    def load_game(self, index: int) -> None:
        message = f"load_game%{index}"
        self._post_message(message)

    def load_game_record(self, record_name: str):
        message = f"load_game_record%{record_name}"
        self._post_message(message)
        print(f"loaded game record: {record_name}")

    def exit_to_title(self) -> None:
        message = f"exit_title"
        self._post_message(message)

    def exit_menu(self):
        message = "exit_menu"
        self._post_message(message)

    def wait_game_start(self):
        message = "wait_game_start"
        self._post_message(message)
        print(f"game started")

    def pause_game(self):
        message = "pause"
        self._post_message(message)

    def resume_game(self):
        message = "resume"
        self._post_message(message)

    def get_surroundings(self, size: int) -> str:
        message = f"get_surroundings%{size}"
        ret_str = self._post_message(message)
        return ret_str

    def get_tile_info(self, x, y):
        message = f"get_tile_info%{x}%{y}"
        ret_str = self._post_message(message)
        return ret_str
