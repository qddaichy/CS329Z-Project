using System.Reflection;
using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using HarmonyLib;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;
using StardewValley.Menus;
using StardewValley.Locations;
using StardewValley.Objects;
using StardewValley.Monsters;
using StardewValley.Tools;
using Microsoft.Xna.Framework.Input;
using Microsoft.Xna.Framework;

namespace CS329z.StateOnly;

public sealed class ModEntry : Mod
{
    public override void Entry(IModHelper helper)
    {
        var config = helper.ReadConfig<ModConfig>();
        AutoFishing.Enabled = config.AutoCatchFishing;
        CutsceneSkip.Enabled = config.AutoSkipCutscenes;
        CutsceneSkip.Monitor = Monitor;
        ClockSpeed.Multiplier = double.IsFinite(config.TimeSpeedMultiplier) && config.TimeSpeedMultiplier >= 0.25 && config.TimeSpeedMultiplier <= 4
            ? config.TimeSpeedMultiplier : 2;
        helper.Events.GameLoop.UpdateTicked += (_, _) => AutoFishing.Update();
        helper.Events.GameLoop.UpdateTicked += (_, _) => CutsceneSkip.Update();
        helper.Events.GameLoop.SaveLoaded += (_, _) => CutsceneSkip.Reset();
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => CutsceneSkip.Reset();
        Type? api = AppDomain.CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType("ActionSpace.actions.ActionsAPI", false))
            .FirstOrDefault(type => type != null);
        if (api == null)
            throw new InvalidOperationException("StarDojo ActionsAPI was not found.");
        Type actions = api.Assembly.GetType("ActionSpace.actions.Actions", true)!;
        PathfindingThreadGuard.Initialize(helper, Monitor);
        StateObservation.Player = AccessTools.Method(actions, "GetPlayerData")
            ?? throw new MissingMethodException("GetPlayerData");
        StateObservation.Tiles = AccessTools.Method(actions, "GetSurroundings")
            ?? throw new MissingMethodException("GetSurroundings");
        StateObservation.Menu = AccessTools.Method(actions, "GetCurrentMenuData");
        StateObservation.GameTime = AccessTools.Method(actions, "GetGameStateData")
            ?? throw new MissingMethodException("GetGameStateData");
        StateObservation.Farm = AccessTools.Method(actions, "GetFarmData")
            ?? throw new MissingMethodException("GetFarmData");
        StateObservation.Mod = this;
        MethodInfo observe = AccessTools.Method(api, "observe_v2")
            ?? throw new MissingMethodException("observe_v2");
        var harmony = new Harmony(ModManifest.UniqueID);
        harmony.Patch(AccessTools.Method(actions, "StartAutoPathing"),
            prefix: new HarmonyMethod(typeof(PathfindingThreadGuard), nameof(PathfindingThreadGuard.Before)),
            postfix: new HarmonyMethod(typeof(PathfindingThreadGuard), nameof(PathfindingThreadGuard.After)));
        harmony.Patch(AccessTools.Method(typeof(Game1), "UpdateGameClock"),
            prefix: new HarmonyMethod(typeof(ClockSpeed), nameof(ClockSpeed.Prefix)));
        harmony.Patch(AccessTools.Method(actions, "exit_menu"),
            prefix: new HarmonyMethod(typeof(DialogueClose), nameof(DialogueClose.Before)),
            postfix: new HarmonyMethod(typeof(DialogueClose), nameof(DialogueClose.After)));
        harmony.Patch(AccessTools.Method(actions, "interact"),
            prefix: new HarmonyMethod(typeof(InteractionTarget), nameof(InteractionTarget.Begin)),
            finalizer: new HarmonyMethod(typeof(InteractionTarget), nameof(InteractionTarget.End)));
        harmony.Patch(observe,
            prefix: new HarmonyMethod(typeof(StateObservation), nameof(StateObservation.Prefix)));
        harmony.Patch(AccessTools.Method(typeof(BobberBar), "update"),
            prefix: new HarmonyMethod(typeof(FishingInput), nameof(FishingInput.Prefix)),
            finalizer: new HarmonyMethod(typeof(FishingInput), nameof(FishingInput.Finalizer)));
        harmony.Patch(AccessTools.Method(typeof(BobberBar), "update"),
            prefix: new HarmonyMethod(typeof(AutoFishing), nameof(AutoFishing.Prefix)));
        harmony.Patch(AccessTools.Method(typeof(CarpenterMenu), "tryToBuild"),
            prefix: new HarmonyMethod(typeof(CarpenterPlacement), nameof(CarpenterPlacement.BeginBuild)),
            finalizer: new HarmonyMethod(typeof(CarpenterPlacement), nameof(CarpenterPlacement.EndBuild)));
        harmony.Patch(AccessTools.Method(typeof(Game1), "getOldMouseX", new[] { typeof(bool) }),
            prefix: new HarmonyMethod(typeof(CarpenterPlacement), nameof(CarpenterPlacement.MouseX)));
        harmony.Patch(AccessTools.Method(typeof(Game1), "getOldMouseY", new[] { typeof(bool) }),
            prefix: new HarmonyMethod(typeof(CarpenterPlacement), nameof(CarpenterPlacement.MouseY)));
        harmony.Patch(AccessTools.Method(typeof(Game1), "getOldMouseX", new[] { typeof(bool) }),
            prefix: new HarmonyMethod(typeof(InteractionTarget), nameof(InteractionTarget.MouseX)));
        harmony.Patch(AccessTools.Method(typeof(Game1), "getOldMouseY", new[] { typeof(bool) }),
            prefix: new HarmonyMethod(typeof(InteractionTarget), nameof(InteractionTarget.MouseY)));
        harmony.Patch(AccessTools.Method(typeof(Game1), "tryToCheckAt", new[] { typeof(Vector2), typeof(Farmer) }),
            prefix: new HarmonyMethod(typeof(InteractionTarget), nameof(InteractionTarget.CheckFacingTile)));
        Monitor.Log("State-only observation enabled: state:<radius>, menu, world, crops (no image).", LogLevel.Info);
    }
}

// Async continuations in the upstream API can construct PathFindController on a
// worker thread. Game collision checks mutate non-concurrent shared collections.
// Dispatch the existing pathfinding operation unchanged onto a game update tick.
internal static class PathfindingThreadGuard
{
    private static readonly ConcurrentQueue<Action> Pending = new();
    private static int MainThreadId;
    private static IMonitor? Monitor;
    private static Request? Active;
    public const int TimeoutSeconds = 20;
    public static bool HasActiveRequest => Active != null;
    public sealed class Request
    {
        public Farmer Player = null!;
        public GameLocation Location = null!;
        public Action<bool> Callback = null!;
        public StardewValley.Pathfinding.PathFindController? Controller;
        public readonly System.Diagnostics.Stopwatch Timer = System.Diagnostics.Stopwatch.StartNew();
        public bool Completed;
    }

    private static void Complete(Request request, bool success)
    {
        if (request.Completed) return;
        request.Completed = true;
        if (request.Controller != null && ReferenceEquals(request.Player.controller, request.Controller))
        {
            request.Player.controller = null;
            request.Player.Halt();
        }
        if (ReferenceEquals(Active, request)) Active = null;
        request.Callback(success);
    }

    public static void Initialize(IModHelper helper, IMonitor monitor)
    {
        MainThreadId = Environment.CurrentManagedThreadId;
        Monitor = monitor;
        helper.Events.GameLoop.UpdateTicked += (_, _) => {
            for (int count = 0; count < 16 && Pending.TryDequeue(out var action); count++)
                action();
            if (Active is Request request)
            {
                if (!Context.IsWorldReady || !ReferenceEquals(Game1.player, request.Player))
                    Complete(request, false);
                else if (!ReferenceEquals(Game1.currentLocation, request.Location))
                    Complete(request, true);
                else if (request.Timer.Elapsed.TotalSeconds >= TimeoutSeconds)
                {
                    Monitor?.Log("Cancelled timed-out API pathfinding.", LogLevel.Warn);
                    Complete(request, false);
                }
            }
        };
        helper.Events.Player.Warped += (_, e) => {
            if (e.IsLocalPlayer && Active is Request request) Complete(request, true);
        };
        helper.Events.GameLoop.DayStarted += (_, _) => {
            if (Active is Request request) Complete(request, false);
        };
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => {
            if (Active is Request request) Complete(request, false);
        };
    }

    public static bool Before(MethodBase __originalMethod, object[] __args, out Request? __state)
    {
        __state = null;
        if (Environment.CurrentManagedThreadId == MainThreadId)
        {
            if (Active is Request previous) Complete(previous, false);
            if (!Context.IsWorldReady) { ((Action<bool>)__args[1])(false); return false; }
            var target = ((Vector2)__args[0]).ToPoint();
            var location = Game1.currentLocation;
            var layer = location.Map?.GetLayer("Back");
            bool inside = layer != null && target.X >= 0 && target.Y >= 0 && target.X < layer.LayerWidth && target.Y < layer.LayerHeight;
            if (!inside && !location.warps.Any(warp => warp.X == target.X && warp.Y == target.Y))
            { ((Action<bool>)__args[1])(false); return false; }
            var request = new Request { Player = Game1.player, Location = location, Callback = (Action<bool>)__args[1] };
            __state = request;
            __args[1] = new Action<bool>(success => Complete(request, success));
            return true;
        }
        var args = (object[])__args.Clone();
        var queuedLocation = Game1.currentLocation;
        var queuedPlayer = Game1.player;
        Pending.Enqueue(() => {
            try
            {
                if (!Context.IsWorldReady || !ReferenceEquals(Game1.currentLocation, queuedLocation) || !ReferenceEquals(Game1.player, queuedPlayer))
                {
                    (args[1] as Action<bool>)?.Invoke(false);
                    return;
                }
                __originalMethod.Invoke(null, args);
            }
            catch (Exception error)
            {
                Monitor?.Log($"Queued pathfinding failed: {error.GetBaseException().Message}", LogLevel.Error);
                (args[1] as Action<bool>)?.Invoke(false);
            }
        });
        Monitor?.Log("Queued API pathfinding on the game thread.", LogLevel.Trace);
        return false;
    }

    public static void After(Request? __state)
    {
        if (__state == null || __state.Completed) return;
        __state.Controller = __state.Player.controller;
        if (__state.Controller == null) { Complete(__state, false); return; }
        Active = __state;
    }
}

// The original action synthesizes a cursor, but its action handler reads the
// previous mouse position. Scope the cursor to the center of the facing tile so
// game interaction uses the intended tile, independently of the real cursor.
internal static class InteractionTarget
{
    [ThreadStatic] private static bool Scoped;
    [ThreadStatic] private static int ScreenX, ScreenY, TargetX, TargetY;
    public sealed class SavedInput
    {
        public bool MouseVisible;
        public float CursorTransparency;
        public MouseState OldMouse;
        public MouseState CurrentMouse;
    }

    private static FieldInfo CurrentMouseField => AccessTools.Field(Game1.input.GetType(), "_currentMouseState")
        ?? throw new MissingFieldException("InputState._currentMouseState");

    public static void Begin(out SavedInput? __state)
    {
        __state = null;
        if (!Context.IsWorldReady || Game1.player == null) return;
        var tile = Game1.player.TilePoint;
        switch (Game1.player.FacingDirection)
        {
            case 0: tile.Y--; break;
            case 1: tile.X++; break;
            case 2: tile.Y++; break;
            case 3: tile.X--; break;
            default: return;
        }
        __state = new SavedInput {
            MouseVisible = Game1.wasMouseVisibleThisFrame,
            CursorTransparency = Game1.mouseCursorTransparency,
            OldMouse = Game1.oldMouseState,
            CurrentMouse = (MouseState)CurrentMouseField.GetValue(Game1.input)!
        };
        ScreenX = tile.X * Game1.tileSize + Game1.tileSize / 2 - Game1.viewport.X;
        ScreenY = tile.Y * Game1.tileSize + Game1.tileSize / 2 - Game1.viewport.Y;
        TargetX = tile.X;
        TargetY = tile.Y;
        Game1.wasMouseVisibleThisFrame = true;
        Game1.mouseCursorTransparency = 1f;
        Scoped = true;
    }

    public static Exception? End(Exception? __exception, SavedInput? __state)
    {
        Scoped = false;
        if (__state != null)
        {
            Game1.wasMouseVisibleThisFrame = __state.MouseVisible;
            Game1.mouseCursorTransparency = __state.CursorTransparency;
            Game1.oldMouseState = __state.OldMouse;
            CurrentMouseField.SetValue(Game1.input, __state.CurrentMouse);
        }
        return __exception;
    }

    public static bool MouseX(bool __0, ref int __result)
    {
        if (!Scoped || __0) return true;
        __result = ScreenX;
        return false;
    }

    public static bool MouseY(bool __0, ref int __result)
    {
        if (!Scoped || __0) return true;
        __result = ScreenY;
        return false;
    }

    public static bool CheckFacingTile(Vector2 __0, ref bool __result)
    {
        if (!Scoped || ((int)Math.Floor(__0.X) == TargetX && (int)Math.Floor(__0.Y) == TargetY)) return true;
        __result = false;
        return false;
    }
}

// The core's forced menu exit can leave a non-question dialogue flagged as
// active after its menu is gone. That prevents time and player movement.
internal static class DialogueClose
{
    public static void Before(out bool __state)
        => __state = Game1.activeClickableMenu is DialogueBox box && !box.isQuestion && !Game1.eventUp;

    public static void After(bool __state)
    {
        if (!__state || Game1.activeClickableMenu != null || Game1.eventUp) return;
        Game1.dialogueUp = false;
        if (!Game1.freezeControls && Game1.player != null)
            Game1.player.CanMove = true;
    }
}

// CarpenterMenu.tryToBuild reads the old cursor position, rather than the click
// coordinates passed to receiveLeftClick. Supply the selected tile only inside
// that game method, including when its normal building-lock callback is delayed.
internal static class CarpenterPlacement
{
    private static CarpenterMenu? PendingMenu;
    private static int TargetX, TargetY;
    private static long Expires;
    [ThreadStatic] private static bool Scoped;
    public static void Select(CarpenterMenu menu, int x, int y)
    {
        PendingMenu = menu; TargetX = x; TargetY = y; Expires = Environment.TickCount64 + 5000;
    }
    public static void BeginBuild(CarpenterMenu __instance)
        => Scoped = ReferenceEquals(__instance, PendingMenu) && Environment.TickCount64 < Expires;
    public static Exception? EndBuild(Exception? __exception)
    {
        Scoped = false; PendingMenu = null;
        return __exception;
    }
    public static bool MouseX(bool __0, ref int __result)
    {
        if (!Scoped || __0) return true;
        __result = TargetX * 64 + 32 - Game1.viewport.X;
        return false;
    }
    public static bool MouseY(bool __0, ref int __result)
    {
        if (!Scoped || __0) return true;
        __result = TargetY * 64 + 32 - Game1.viewport.Y;
        return false;
    }
}

public sealed class ModConfig
{
    public bool AutoCatchFishing { get; set; } = true;
    public bool AutoSkipCutscenes { get; set; } = true;
    public double TimeSpeedMultiplier { get; set; } = 2;
}

// Use the game's own skip path, which runs actionsOnSkip and event cleanup.
// Festivals and weddings are interactive events rather than ordinary NPC scenes.
// Attempt once per event; a failed skip must not flood the log or loop forever.
internal static class CutsceneSkip
{
    internal static bool Enabled = true;
    internal static IMonitor? Monitor;
    internal static int Attempted, Skipped, Failed;
    private static Event? Current;
    private static int Ticks;
    private static bool Tried;

    internal static void Reset()
    {
        Current = null; Ticks = 0; Tried = false;
        Attempted = Skipped = Failed = 0;
    }

    internal static void Update()
    {
        if (!Enabled || !Context.IsWorldReady || !Game1.eventUp || Game1.CurrentEvent == null)
        {
            Current = null; Ticks = 0; Tried = false;
            return;
        }
        var scene = Game1.CurrentEvent;
        if (!ReferenceEquals(Current, scene))
        {
            Current = scene; Ticks = 0; Tried = false;
        }
        if (scene.isFestival || scene.isWedding || Tried || ++Ticks < 5) return;
        Tried = true;
        Attempted++;
        try
        {
            string id = scene.id ?? "unknown";
            scene.skipEvent();
            Skipped++;
            Monitor?.Log($"Auto-skipped story event {id} via the game skip handler.", LogLevel.Info);
        }
        catch (Exception error)
        {
            Failed++;
            Monitor?.Log($"Could not auto-skip story event: {error}", LogLevel.Warn);
        }
    }
}

// Scale only the game clock's elapsed time. The normal clock method still
// decides when time passes and performs all ordinary ten-minute updates.
internal static class ClockSpeed
{
    internal static double Multiplier = 2;

    public static void Prefix(ref GameTime __0)
    {
        if (Multiplier == 1) return;
        __0 = new GameTime(__0.TotalGameTime,
            TimeSpan.FromTicks((long)(__0.ElapsedGameTime.Ticks * Multiplier)),
            __0.IsRunningSlowly);
    }
}

// Simplified experiment rule: preserve the normal catch resolution, but prevent
// the fishing bar from failing and complete it after ten seconds of game updates.
internal static class AutoFishing
{
    internal static bool Enabled = true;
    private const double RequiredSeconds = 10;
    private sealed class Timer { public double Elapsed; }
    private sealed class CastState { public bool WasFishing; public bool Hooked; public bool Collected; }
    private static readonly ConditionalWeakTable<BobberBar, Timer> Timers = new();
    private static readonly ConditionalWeakTable<FishingRod, CastState> Casts = new();
    private static readonly FieldInfo Progress = AccessTools.Field(typeof(BobberBar), "distanceFromCatching")
        ?? throw new MissingFieldException("BobberBar.distanceFromCatching");
    private static readonly FieldInfo Nibbling = AccessTools.Field(typeof(FishingRod), "isNibbling")
        ?? throw new MissingFieldException("FishingRod.isNibbling");
    private static readonly FieldInfo IsFishing = AccessTools.Field(typeof(FishingRod), "isFishing")
        ?? throw new MissingFieldException("FishingRod.isFishing");
    private static readonly FieldInfo FishCaught = AccessTools.Field(typeof(FishingRod), "fishCaught")
        ?? throw new MissingFieldException("FishingRod.fishCaught");

    // Act within the game's update loop so the bite cannot expire between an
    // observation and an LLM decision. Use the game's normal rod operations.
    public static void Update()
    {
        if (!Enabled || !Context.IsWorldReady || Game1.paused ||
            Game1.player.CurrentTool is not FishingRod rod) return;
        var state = Casts.GetOrCreateValue(rod);
        bool fishing = IsFishing.GetValue(rod) is true;
        bool nibbling = Nibbling.GetValue(rod) is true;
        bool caught = FishCaught.GetValue(rod) is true;
        if (fishing && !state.WasFishing && !nibbling && !caught)
            state.Hooked = state.Collected = false;
        state.WasFishing = fishing;
        if (Game1.activeClickableMenu != null) return;
        if (caught && !state.Collected)
        {
            state.Collected = true;
            rod.doneHoldingFish(Game1.player, false);
        }
        else if (fishing && nibbling && !state.Hooked && !caught)
        {
            state.Hooked = true;
            rod.DoFunction(Game1.currentLocation, 0, 0, 0, Game1.player);
        }
    }

    public static double Remaining(BobberBar bar) => Math.Max(0, RequiredSeconds - Timers.GetOrCreateValue(bar).Elapsed);

    public static void Prefix(BobberBar __instance, GameTime __0)
    {
        if (!Enabled || Game1.paused || Game1.activeClickableMenu != __instance) return;
        var timer = Timers.GetOrCreateValue(__instance);
        timer.Elapsed += __0.ElapsedGameTime.TotalSeconds;
        float progress = (float)Progress.GetValue(__instance)!;
        // The game's update can subtract progress before checking for success.
        // Leave enough headroom for that subtraction; its own update clamps to 1.
        Progress.SetValue(__instance, timer.Elapsed >= RequiredSeconds ? 2f : Math.Clamp(progress, 0.1f, 0.9f));
    }
}

// Emulate one held or released mouse button only while the fishing bar updates.
// Used when the optional manual fishing mode is enabled.
internal static class FishingInput
{
    private static readonly object Sync = new();
    private static long Until;
    private static bool Pressed;

    public static object Set(string command)
    {
        var parts = command.Split(':');
        if (parts.Length != 3 || (parts[1] != "0" && parts[1] != "1") ||
            !int.TryParse(parts[2], out int ms) || ms < 50 || ms > 1000)
            return new { ok = false, error = "Expected fish_input:<0|1>:<50..1000 milliseconds>." };
        if (Game1.activeClickableMenu is not BobberBar)
            return new { ok = false, error = "The fishing minigame is not open." };
        lock (Sync)
        {
            Pressed = parts[1] == "1";
            Until = Environment.TickCount64 + ms;
        }
        return new { ok = true, pressed = parts[1] == "1", duration_ms = ms };
    }

    public static void Prefix(out MouseState? __state)
    {
        __state = null;
        lock (Sync)
        {
            if (Environment.TickCount64 >= Until) return;
            var previous = Game1.oldMouseState;
            __state = previous;
            Game1.oldMouseState = new MouseState(previous.X, previous.Y, previous.ScrollWheelValue,
                Pressed ? ButtonState.Pressed : ButtonState.Released, previous.MiddleButton,
                previous.RightButton, previous.XButton1, previous.XButton2);
        }
    }

    public static Exception? Finalizer(Exception? __exception, MouseState? __state)
    {
        if (__state.HasValue) Game1.oldMouseState = __state.Value;
        return __exception;
    }
}

internal static class StateObservation
{
    internal static MethodInfo Player = null!;
    internal static MethodInfo Tiles = null!;
    internal static MethodInfo? Menu;
    internal static MethodInfo GameTime = null!;
    internal static MethodInfo Farm = null!;
    internal static Mod Mod = null!;

    private static object NpcState()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var npcs = Utility.getAllCharacters()
            .DistinctBy(npc => (npc.Name, npc.currentLocation?.NameOrUniqueName, npc.TilePoint.X, npc.TilePoint.Y))
            .Select(npc => {
            Game1.player.friendshipData.TryGetValue(npc.Name, out var friendship);
            return new { name = npc.Name, location = npc.currentLocation?.NameOrUniqueName,
                position = new[] { npc.TilePoint.X, npc.TilePoint.Y },
                friendship = Game1.player.getFriendshipLevelForNPC(npc.Name),
                talked_today = Game1.player.hasPlayerTalkedToNPC(npc.Name),
                gifts_today = friendship?.GiftsToday ?? 0 };
        }).ToList();
        return new { available = true, scope = "all_enumerated_characters_original_mod", npcs };
    }

    private static object FarmState()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var farm = Game1.getFarm();
        var animalLocations = new Dictionary<long, string>();
        void RecordAnimals(GameLocation location)
        {
            foreach (var animal in location.animals.SelectMany(group => group.Values))
                animalLocations[animal.myID.Value] = location.NameOrUniqueName;
        }
        RecordAnimals(farm);
        foreach (var building in farm.buildings)
            if (building.GetIndoors() is GameLocation indoors)
                RecordAnimals(indoors);

        var data = JObject.FromObject(Farm.Invoke(null, new object[] { Mod })!);
        data["Animals"] = JToken.FromObject(farm.getAllFarmAnimals().Select(animal => new {
            Type = animal.type.Value, animal.Name, Id = animal.myID.Value.ToString(),
            Location = animalLocations.TryGetValue(animal.myID.Value, out var location) ? location : null,
            Position = animal.TilePoint, Friendship = animal.friendshipTowardFarmer.Value,
            IsAdult = animal.isAdult(), CurrentProduce = animal.currentProduce.Value,
            Happiness = animal.happiness.Value, isTouched = animal.wasPet.Value
        }).ToList());
        return new { available = true, scope = "farm_original_mod", farm = data };
    }

    private static object LocationDetails()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var location = Game1.currentLocation;
        var doors = location.doors.FieldDict.Select(door => new {
            target = door.Value.Value, position = new[] { door.Key.X, door.Key.Y }
        }).ToList();
        var counters = new Dictionary<string, int[]> {
            ["SeedShop"] = new[] { 4, 18 }, ["AnimalShop"] = new[] { 12, 15 },
            ["JojaMart"] = new[] { 10, 25 }, ["Blacksmith"] = new[] { 3, 14 },
            ["ScienceHouse"] = new[] { 8, 19 }, ["FishShop"] = new[] { 5, 5 }
        };
        var furniture = location.furniture.Select(item => {
            var box = item.GetBoundingBox();
            int offset = item is BedFurniture bed ? bed.bedTileOffset : 0;
            return new { name = item.Name, position = new[] { (int)item.TileLocation.X, (int)item.TileLocation.Y },
                bed_position = item is BedFurniture ? new[] { (int)item.TileLocation.X + offset, (int)item.TileLocation.Y + offset } : null,
                bounds_tiles = new { left = box.Left / Game1.tileSize, top = box.Top / Game1.tileSize,
                    right = box.Right / Game1.tileSize, bottom = box.Bottom / Game1.tileSize } };
        }).ToList();
        return new { available = true, scope = "current_location", location = location.NameOrUniqueName,
            doors, exits = ExitData(location), counters = counters.Where(pair => pair.Key == location.Name).Select(pair => new {
                name = pair.Key + " Counter", position = pair.Value
            }).ToList(), furniture,
            limitations = "Counter positions are the original mod's fixed coordinates; layouts may differ with mods." };
    }

    private static string? ItemName(string? id)
    {
        if (string.IsNullOrWhiteSpace(id) || id == "0")
            return null;
        try { return ItemRegistry.Create(id).Name; }
        catch { return null; }
    }

    private static object FarmingState()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var soils = Game1.currentLocation.terrainFeatures.Values.OfType<HoeDirt>().Select(dirt =>
        {
            var crop = dirt.crop;
            var seedId = crop?.netSeedIndex.Value;
            var harvestId = crop?.indexOfHarvest.Value;
            var fertilizerId = dirt.fertilizer.Value;
            return new
            {
                position = new[] { (int)dirt.Tile.X, (int)dirt.Tile.Y },
                watered = dirt.isWatered(),
                fertilizer_id = fertilizerId,
                fertilizer_name = ItemName(fertilizerId),
                crop = crop == null ? null : new
                {
                    seed_id = seedId,
                    seed_name = ItemName(seedId),
                    harvest_id = harvestId,
                    harvest_name = ItemName(harvestId),
                    growth_phase = crop.currentPhase.Value,
                    ready_to_harvest = crop.forageCrop.Value ? (bool?)null :
                        !crop.dead.Value && crop.currentPhase.Value >= crop.phaseDays.Count - 1 &&
                        (!crop.fullyGrown.Value || crop.dayOfCurrentPhase.Value <= 0),
                    fully_grown = crop.fullyGrown.Value,
                    dead = crop.dead.Value,
                    forage_crop = crop.forageCrop.Value
                }
            };
        }).ToArray();
        return new { available = true, scope = "current_location_all_soil",
                     location = Game1.currentLocation.NameOrUniqueName, soils };
    }

    private static object EconomyState()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var inventory = Game1.player.Items.Select((item, index) => item == null ? null : new
        {
            slot = index, id = item.QualifiedItemId, name = item.Name, quantity = item.Stack,
            quality = item.Quality, shippable = item.canBeShipped(),
            base_unit_sell_price = item is StardewValley.Object obj ? (int?)obj.sellToStorePrice(-1L) : null
        }).Where(item => item != null).ToArray();
        var atFarm = Game1.currentLocation is Farm;
        var bin = atFarm ? Game1.getFarm().getShippingBin(Game1.player).Select(item => new
        {
            id = item.QualifiedItemId, name = item.Name, quantity = item.Stack, quality = item.Quality
        }).ToArray() : null;
        var binLocation = atFarm ? Game1.getFarm().GetStarterShippingBinLocation() : (Microsoft.Xna.Framework.Vector2?)null;
        return new { available = true, inventory, shipping_bin = bin,
                     shipping_bin_position = binLocation == null ? null : new[] { (int)binLocation.Value.X, (int)binLocation.Value.Y },
                     shipping_scope = atFarm ? "current_farm_pending_items" : "only_available_on_farm" };
    }

    private static object NearbyMachines()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var player = Game1.player.TilePoint;
        var machines = Game1.currentLocation.objects.Pairs
            .Where(pair => pair.Value.bigCraftable.Value && Math.Abs(pair.Key.X - player.X) <= 8 && Math.Abs(pair.Key.Y - player.Y) <= 8)
            .Select(pair => new {
                position = new[] { (int)pair.Key.X, (int)pair.Key.Y }, name = pair.Value.Name,
                id = pair.Value.QualifiedItemId,
                ready = pair.Value.readyForHarvest.Value,
                processing = pair.Value.heldObject.Value != null && !pair.Value.readyForHarvest.Value,
                minutes_until_ready = pair.Value.heldObject.Value != null && !pair.Value.readyForHarvest.Value ? (int?)pair.Value.MinutesUntilReady : null,
                output = pair.Value.readyForHarvest.Value ? pair.Value.heldObject.Value?.Name : null
            }).ToArray();
        return new { available = true, scope = "current_location_within_8_tiles", machines };
    }

    private static object KnownRecipes()
    {
        if (!Context.IsWorldReady) return new { available = false, error = "No loaded game." };
        var recipes = Game1.player.craftingRecipes.Keys.Select(name => {
            try {
                var recipe = new CraftingRecipe(name);
                return (object)new { name, display_name = recipe.DisplayName,
                    can_craft = recipe.doesFarmerHaveIngredientsInInventory(),
                    ingredients = recipe.recipeList.Select(pair => new { id = pair.Key,
                        name = pair.Key.StartsWith("-") ? null : ItemName(pair.Key), quantity = pair.Value }).ToArray() };
            }
            catch { return new { name, error = "Recipe details unavailable." }; }
        }).ToArray();
        return new { available = true, recipes };
    }

    private static object QuestState()
    {
        if (!Context.IsWorldReady) return new { available = false, error = "No loaded game." };
        var quests = Game1.player.questLog.Where(q => !q.IsHidden())
            .Select(q => new { id = q.id.Value, title = q.questTitle,
                description = q.questDescription, objective = q.currentObjective,
                completed = q.completed.Value, daily = q.dailyQuest.Value,
                days_left = q.dailyQuest.Value ? (int?)q.daysLeft.Value : null,
                money_reward = q.GetMoneyReward() }).ToArray();
        return new { available = true, quests };
    }

    private static object OpenQuestLog()
    {
        if (!Context.IsWorldReady || Game1.activeClickableMenu != null || Game1.eventUp || !Game1.player.CanMove)
            return new { ok = false, error = "Close the current menu and wait until the player can move." };
        Game1.activeClickableMenu = new QuestLog();
        return new { ok = true, note = "Quest journal opened." };
    }

    private static object QuestClick(string command)
    {
        if (Game1.activeClickableMenu is not QuestLog menu)
            return new { ok = false, error = "The quest journal is not open." };
        var args = command.Split(':');
        if (args.Length != 3 || !int.TryParse(args[1], out int action) || !int.TryParse(args[2], out int index))
            return new { ok = false, error = "Invalid quest-menu action." };
        var shown = AccessTools.Field(typeof(QuestLog), "_shownQuest")?.GetValue(menu) as StardewValley.Quests.IQuest;
        var pages = AccessTools.Field(typeof(QuestLog), "pages")?.GetValue(menu) as List<List<StardewValley.Quests.IQuest>>;
        int page = (int)(AccessTools.Field(typeof(QuestLog), "currentPage")?.GetValue(menu) ?? -1);
        if (pages == null || page < 0 || page >= pages.Count ||
            (action == 0 && (shown != null || index < 0 || index >= pages[page].Count)) ||
            (action == 1 && (shown == null || !shown.HasMoneyReward())) ||
            (action == 2 && (shown != null || page + 1 >= pages.Count)) ||
            (action == 3 && shown == null && page <= 0))
            return new { ok = false, error = "Quest action is unavailable on this page." };
        ClickableComponent? button = action switch {
            0 when index >= 0 && index < menu.questLogButtons.Count => menu.questLogButtons[index],
            1 => menu.rewardBox, 2 => menu.forwardButton, 3 => menu.backButton, _ => null
        };
        if (button == null || !button.visible)
            return new { ok = false, error = "Quest button is not visible or index is invalid." };
        int x = button.bounds.Center.X, y = button.bounds.Center.Y;
        if (x < 0 || y < 0 || x >= Game1.uiViewport.Width || y >= Game1.uiViewport.Height)
            return new { ok = false, error = "Quest button is outside the visible menu." };
        menu.receiveLeftClick(x, y);
        return new { ok = true, note = "Clicked the journal; inspect the game state for the outcome." };
    }

    private static object RequestSleep()
    {
        if (!Context.IsWorldReady || Game1.currentLocation is not FarmHouse house)
            return new { ok = false, error = "You must be inside your farmhouse." };
        if (Game1.activeClickableMenu != null || Game1.eventUp || !Game1.player.CanMove)
            return new { ok = false, error = "Close menus and wait until the player can move." };
        var tile = Game1.player.TilePoint;
        var bed = BedFurniture.GetBedAtTile(house, tile.X, tile.Y);
        if (bed == null || bed != house.GetPlayerBed())
            return new { ok = false, error = "Stand on your farmhouse bed first." };
        house.createQuestionDialogue("Go to sleep for the night?", house.createYesNoResponses(), "Sleep");
        return new { ok = true, note = "Answer the sleep confirmation; the game performs the overnight transition." };
    }

    private static object ConfirmSleep()
    {
        if (!Context.IsWorldReady || Game1.currentLocation is not FarmHouse ||
            Game1.currentLocation.lastQuestionKey != "Sleep" ||
            Game1.activeClickableMenu is not DialogueBox dialogue || !dialogue.isQuestion)
            return new { ok = false, error = "The farmhouse sleep confirmation must be open." };
        if (dialogue.transitioning || dialogue.safetyTimer > 0)
            return new { ok = false, error = "Sleep dialogue is not ready; wait briefly and retry." };
        if (dialogue.responses == null || dialogue.responses.Length == 0 || dialogue.responses[0].responseKey != "Yes")
            return new { ok = false, error = "The expected Yes response is unavailable." };
        // Reveal the text, then use the original game's question-selection path.
        dialogue.characterIndexInDialogue = dialogue.getCurrentString().Length - 1;
        var actions = Player.DeclaringType!;
        var select = AccessTools.Method(actions, "select_dialogue") ?? throw new MissingMethodException("select_dialogue");
        select.Invoke(null, new object[] { 0, Mod });
        return new { ok = true, note = "Selected Yes; verify the overnight transition." };
    }

    private static object LevelUpAction(string command)
    {
        if (Game1.activeClickableMenu is not LevelUpMenu menu)
            return new { ok = false, error = "No level-up menu is open." };
        var parts = command.Split(':');
        if (parts.Length != 2 || !int.TryParse(parts[1], out int choice) || choice < 0 || choice > 2)
            return new { ok = false, error = "Use 0 to continue, or 1/2 for left/right profession." };
        bool profession = (bool)(AccessTools.Field(typeof(LevelUpMenu), "isProfessionChooser")!.GetValue(menu) ?? false);
        if ((profession && choice == 0) || (!profession && choice != 0))
            return new { ok = false, error = profession ? "Choose a profession (1 or 2)." : "Use 0 to continue." };
        if (!(bool)AccessTools.Method(typeof(LevelUpMenu), "CanReceiveInput")!.Invoke(menu, null)!)
            return new { ok = false, error = "Level-up menu is not ready; wait briefly and retry." };
        string field = choice == 0 ? "okButton" : choice == 1 ? "leftProfession" : "rightProfession";
        var button = AccessTools.Field(typeof(LevelUpMenu), field)!.GetValue(menu) as ClickableComponent;
        if (button == null || !button.visible)
            return new { ok = false, error = "The requested menu button is unavailable." };
        if (choice == 0)
            AccessTools.Method(typeof(LevelUpMenu), "okButtonClicked")!.Invoke(menu, null);
        else
        {
            // In Stardew 1.6 profession clicks are processed in update(), not
            // receiveLeftClick(). Apply the same guarded selection path as that
            // handler, using the player's explicit left/right choice.
            if (!menu.readyToClose())
                return new { ok = false, error = "Level-up menu is not ready; wait briefly and retry." };
            var choices = AccessTools.Field(typeof(LevelUpMenu), "professionsToChoose")!.GetValue(menu) as List<int>;
            if (choices == null || choices.Count != 2)
                return new { ok = false, error = "Profession choices are not ready." };
            int professionId = choices[choice - 1];
            Game1.player.professions.Add(professionId);
            AccessTools.Method(typeof(LevelUpMenu), "getImmediateProfessionPerk")!.Invoke(menu, new object[] { professionId });
            foreach (string name in new[] { "isActive", "informationUp", "isProfessionChooser" })
                AccessTools.Field(typeof(LevelUpMenu), name)!.SetValue(menu, false);
            AccessTools.Method(typeof(LevelUpMenu), "RemoveLevelFromLevelList")!.Invoke(menu, null);
            menu.height = 512;
        }
        return new { ok = true, note = "Clicked the level-up menu; observe the result." };
    }

    private static object NearbyEntities()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var player = Game1.player.TilePoint;
        bool Visible(int x, int y) => Math.Abs(x - player.X) <= 8 && Math.Abs(y - player.Y) <= 8;
        var monsters = Game1.currentLocation.characters.OfType<Monster>()
            .Where(monster => Visible(monster.TilePoint.X, monster.TilePoint.Y))
            .Select(monster => new { name = monster.Name, position = new[] { monster.TilePoint.X, monster.TilePoint.Y } }).ToArray();
        var animals = Game1.currentLocation.animals.SelectMany(group => group.Values)
            .Where(animal => Visible(animal.TilePoint.X, animal.TilePoint.Y))
            .Select(animal => new { name = animal.Name, type = animal.type.Value,
                id = animal.myID.Value.ToString(), petted_today = animal.wasPet.Value,
                position = new[] { animal.TilePoint.X, animal.TilePoint.Y } }).ToArray();
        return new { available = true, location = Game1.currentLocation.NameOrUniqueName,
                     scope = "current_location_within_8_tiles", monsters, animals };
    }

    private static object PetAnimal(string command)
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null || Game1.activeClickableMenu != null || !Game1.player.CanMove)
            return new { ok = false, error = "The player must be free to interact." };
        var parts = command.Split(':');
        if (parts.Length != 2 || !int.TryParse(parts[1], out int direction) || direction < 0 || direction > 3)
            return new { ok = false, error = "Direction must be 0 through 3." };
        var point = Game1.player.TilePoint;
        if (direction == 0) point.Y--; else if (direction == 1) point.X++;
        else if (direction == 2) point.Y++; else point.X--;
        Game1.player.FacingDirection = direction;
        var target = new Microsoft.Xna.Framework.Rectangle(point.X * 64, point.Y * 64, 64, 64);
        bool petted = Game1.currentLocation.CheckPetAnimal(target, Game1.player);
        return new { ok = petted, note = petted ? "The game petted an animal." : "No unpetted animal in the adjacent tile." };
    }

    private static object FishingState()
    {
        if (!Context.IsWorldReady)
            return new { available = false, error = "No loaded game." };
        var rod = Game1.player.CurrentTool as FishingRod;
        var bar = Game1.activeClickableMenu as BobberBar;
        object? Field(object? obj, string name) => obj == null ? null : AccessTools.Field(obj.GetType(), name)?.GetValue(obj);
        return new { available = true, rod_selected = rod != null,
            fishing = Field(rod, "isFishing"), nibbling = Field(rod, "isNibbling"),
            catch_ready = Field(rod, "fishCaught"),
            timing_cast = Field(rod, "isTimingCast"), reeling = Field(rod, "isReeling"),
            minigame_open = bar != null, auto_catch = AutoFishing.Enabled,
            auto_hook = AutoFishing.Enabled, auto_collect = AutoFishing.Enabled,
            auto_catch_seconds_remaining = bar != null && AutoFishing.Enabled ? (double?)AutoFishing.Remaining(bar) : null,
            minigame = bar == null ? null : new { fish_position = Field(bar, "bobberPosition"),
                bar_position = Field(bar, "bobberBarPos"), bar_height = Field(bar, "bobberBarHeight"),
                catch_progress = Field(bar, "distanceFromCatching") } };
    }

    private static object? CarpenterView(CarpenterMenu menu)
    {
        if (!menu.onFarm || menu.TargetLocation == null || menu.TargetLocation.Map == null) return null;
        var layer = menu.TargetLocation.Map.GetLayer("Back");
        if (layer == null) return null;
        int left = Math.Max(0, Game1.viewport.X / 64), top = Math.Max(0, Game1.viewport.Y / 64);
        int right = Math.Min(layer.LayerWidth - 1, (Game1.viewport.X + Game1.viewport.Width - 1) / 64);
        int bottom = Math.Min(layer.LayerHeight - 1, (Game1.viewport.Y + Game1.viewport.Height - 1) / 64);
        var blocked = new List<int[]>();
        for (int y = top; y <= bottom; y++) for (int x = left; x <= right; x++)
            if (!menu.TargetLocation.isBuildable(new Vector2(x, y), false)) blocked.Add(new[] { x, y });
        return new { bounds = new[] { left, top, right, bottom }, blocked_tiles = blocked,
            note = "Per-tile buildability within the visible viewport; placement still requires the game to validate the entire footprint." };
    }

    private static object CombatStats()
    {
        if (!Context.IsWorldReady)
            return new { available = false, error = "No loaded game." };
        return new { available = true, monsters_killed = Game1.stats.specificMonstersKilled
            .Select(pair => new { name = pair.Key, count = pair.Value }).ToArray() };
    }

    private static object FishHook()
    {
        if (!Context.IsWorldReady || Game1.activeClickableMenu != null ||
            Game1.player.CurrentTool is not FishingRod rod ||
            AccessTools.Field(typeof(FishingRod), "isNibbling")?.GetValue(rod) is not true)
            return new { ok = false, error = "A selected fishing rod must have a fish nibbling." };
        // Invoke the game's ordinary rod-use operation, bypassing the core
        // server's wait-for-UsingTool gate, which otherwise blocks reeling.
        rod.DoFunction(Game1.currentLocation, 0, 0, 0, Game1.player);
        return new { ok = true, note = "Reel input sent; inspect fishing state for the result." };
    }

    private static object FishCollect()
    {
        if (!Context.IsWorldReady || Game1.activeClickableMenu != null ||
            Game1.player.CurrentTool is not FishingRod rod ||
            AccessTools.Field(typeof(FishingRod), "fishCaught")?.GetValue(rod) is not true)
            return new { ok = false, error = "No catch is ready to collect." };
        rod.doneHoldingFish(Game1.player, false);
        return new { ok = true, note = "Collect input sent; inspect inventory and any resulting menu." };
    }

    private static object MenuState(bool includeDetails)
    {
        if (!Context.IsWorldReady)
            return new { available = false, error = "No loaded game." };
        var active = Game1.activeClickableMenu;
        if (active == null)
            return new { available = true, is_open = false, menu_type = "No Menu", details_available = true };
        if (!includeDetails)
            return new { available = true, is_open = true, menu_type = active.GetType().Name };
        if (active is LevelUpMenu level)
        {
            object? Field(string name) => AccessTools.Field(typeof(LevelUpMenu), name)?.GetValue(level);
            return new { available = true, is_open = true, menu_type = "LevelUpMenu", details_available = true,
                skill = Field("currentSkill"), level = Field("currentLevel"), profession_choice = Field("isProfessionChooser"),
                ready = AccessTools.Method(typeof(LevelUpMenu), "CanReceiveInput")!.Invoke(level, null),
                left_description = Field("leftProfessionDescription"), right_description = Field("rightProfessionDescription"),
                actions = "level_up_menu: 0=continue, 1=left profession, 2=right profession" };
        }
        if (active is QuestLog questMenu)
        {
            var shown = AccessTools.Field(typeof(QuestLog), "_shownQuest")?.GetValue(questMenu) as StardewValley.Quests.IQuest;
            var page = (int)(AccessTools.Field(typeof(QuestLog), "currentPage")?.GetValue(questMenu) ?? -1);
            var pages = AccessTools.Field(typeof(QuestLog), "pages")?.GetValue(questMenu) as List<List<StardewValley.Quests.IQuest>>;
            var pageItems = pages != null && page >= 0 && page < pages.Count ? pages[page] : null;
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                details_available = true, page, selected = shown?.GetName(),
                quest_buttons = pageItems?.Select((quest, index) => new { index, name = quest.GetName(),
                    completed = quest.ShouldDisplayAsComplete(), reward = quest.GetMoneyReward() }).ToArray(),
                reward_visible = shown != null && shown.HasMoneyReward(),
                next_visible = shown == null && pages != null && page + 1 < pages.Count,
                previous_visible = shown != null || page > 0 };
        }
        if (active is CarpenterMenu carpenter)
        {
            var blueprint = carpenter.Blueprint;
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                details_available = true, stage = carpenter.onFarm ? "farm_placement" : "choose_blueprint",
                blueprint = blueprint == null ? null : new { index = blueprint.Index, id = blueprint.Id,
                    name = blueprint.DisplayName, description = blueprint.Description,
                    size = new[] { blueprint.TilesWide, blueprint.TilesHigh },
                    cost = blueprint.Data.BuildCost },
                can_build = !carpenter.onFarm && carpenter.CanBuildCurrentBlueprint(),
                ingredients = carpenter.ingredients?.Select(item => new { name = item.Name, quantity = item.Stack }).ToArray(),
                camera = carpenter.onFarm ? new { x = Game1.viewport.X, y = Game1.viewport.Y,
                    width = Game1.viewport.Width, height = Game1.viewport.Height } : null,
                placement_view = CarpenterView(carpenter) };
        }
        if (active is PurchaseAnimalsMenu animalMenu)
        {
            var choices = animalMenu.animalsToPurchase.Select((item, index) => new {
                index, name = item.hoverText, price = item.item?.salePrice(), visible = item.visible
            }).ToArray();
            var houses = animalMenu.onFarm && animalMenu.TargetLocation != null ?
                animalMenu.TargetLocation.buildings.Select((building, index) => new {
                    index, type = building.buildingType.Value,
                    position = new[] { building.tileX.Value, building.tileY.Value },
                    under_construction = building.isUnderConstruction()
                }).ToArray() : null;
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                details_available = true, stage = animalMenu.namingAnimal ? "name" : animalMenu.onFarm ? "choose_home" : "choose_animal",
                camera = animalMenu.onFarm ? new { x = Game1.viewport.X, y = Game1.viewport.Y,
                    width = Game1.viewport.Width, height = Game1.viewport.Height } : null,
                choices, buildings = houses };
        }
        if (active is ShopMenu shop)
        {
            var stock = shop.forSale.Select((item, index) => new
            {
                option_index = index + 1, name = item.DisplayName,
                price = shop.itemPriceAndStock.TryGetValue(item, out var data) ? (int?)data.Price : null,
                stock = shop.itemPriceAndStock.TryGetValue(item, out var info) ? (int?)info.Stock : null
            }).ToArray();
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                         details_available = true, shop_id = shop.ShopId, currency = shop.currency, stock };
        }
        if (Menu == null)
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                         details_available = false, error = "Core mod does not expose menu details." };
        try
        {
            var details = Menu.Invoke(null, null);
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                         details_available = details != null, details };
        }
        catch (Exception error)
        {
            return new { available = true, is_open = true, menu_type = active.GetType().Name,
                         details_available = false, error = error.GetBaseException().Message };
        }
    }

    private static object AnimalMenuClick(string command)
    {
        if (Game1.activeClickableMenu is not PurchaseAnimalsMenu menu)
            return new { ok = false, error = "The animal purchase menu is not open." };
        var parts = command.Split(':');
        if (parts.Length != 3 || !int.TryParse(parts[1], out int action) || !int.TryParse(parts[2], out int index))
            return new { ok = false, error = "Invalid animal menu action." };
        int x, y;
        if (action == 0 && !menu.onFarm && index >= 0 && index < menu.animalsToPurchase.Count)
        {
            var option = menu.animalsToPurchase[index];
            if (!option.visible) return new { ok = false, error = "Animal option is not visible; scroll first." };
            x = option.bounds.Center.X; y = option.bounds.Center.Y;
        }
        else if (action == 1 && menu.onFarm && !menu.namingAnimal && menu.TargetLocation != null &&
                 index >= 0 && index < menu.TargetLocation.buildings.Count())
        {
            var building = menu.TargetLocation.buildings.ElementAt(index);
            var box = building.GetBoundingBox();
            x = (int)Utility.ModifyCoordinateForUIScale(box.Center.X - Game1.viewport.X);
            y = (int)Utility.ModifyCoordinateForUIScale(box.Center.Y - Game1.viewport.Y);
            if (x < 0 || y < 0 || x >= Game1.uiViewport.Width || y >= Game1.uiViewport.Height)
                return new { ok = false, error = "Building is outside the visible farm view; pan the camera first." };
        }
        else if ((action == 2 || action == 3) && menu.onFarm && menu.namingAnimal)
        {
            var button = action == 2 ? menu.randomButton : menu.doneNamingButton;
            if (button == null || !button.visible) return new { ok = false, error = "Button unavailable." };
            x = button.bounds.Center.X; y = button.bounds.Center.Y;
        }
        else return new { ok = false, error = "Action is unavailable at this purchase stage." };
        if (x < 0 || y < 0 || x >= Game1.uiViewport.Width || y >= Game1.uiViewport.Height)
            return new { ok = false, error = "The target is outside the visible menu." };
        menu.receiveLeftClick(x, y);
        return new { ok = true, note = "Clicked the animal purchase menu; inspect it for the result." };
    }

    private static object AnimalMenuPan(string command)
    {
        if (Game1.activeClickableMenu is not PurchaseAnimalsMenu menu || !menu.onFarm || menu.namingAnimal)
            return new { ok = false, error = "Farm view for selecting an animal home is not open." };
        var parts = command.Split(':');
        if (parts.Length != 3 || !int.TryParse(parts[1], out int dx) || !int.TryParse(parts[2], out int dy) ||
            Math.Abs(dx) > 256 || Math.Abs(dy) > 256 || (dx != 0 && dy != 0))
            return new { ok = false, error = "Pan one axis by at most 256 pixels." };
        Game1.panScreen(dx, dy);
        return new { ok = true };
    }

    private static object CarpenterAction(string command)
    {
        if (Game1.activeClickableMenu is not CarpenterMenu menu)
            return new { ok = false, error = "The carpenter menu is not open." };
        var parts = command.Split(':');
        if (parts.Length != 4 || !int.TryParse(parts[1], out int action) ||
            !int.TryParse(parts[2], out int x) || !int.TryParse(parts[3], out int y))
            return new { ok = false, error = "Invalid carpenter action." };
        if (action == 5 && menu.onFarm)
        {
            if (Math.Abs(x) > 256 || Math.Abs(y) > 256 || (x != 0 && y != 0))
                return new { ok = false, error = "Pan one axis by at most 256 pixels." };
            Game1.panScreen(x, y);
            return new { ok = true, note = "Farm-view camera moved." };
        }
        int screenX, screenY;
        if (action == 4 && menu.onFarm && x >= 0 && y >= 0)
        {
            screenX = (int)Utility.ModifyCoordinateForUIScale(x * 64 + 32 - Game1.viewport.X);
            screenY = (int)Utility.ModifyCoordinateForUIScale(y * 64 + 32 - Game1.viewport.Y);
        }
        else if (!menu.onFarm && action >= 0 && action <= 3)
        {
            var button = action switch { 0 => menu.backButton, 1 => menu.forwardButton,
                                         2 => menu.okButton, _ => menu.cancelButton };
            if (button == null || !button.visible) return new { ok = false, error = "Button unavailable." };
            screenX = button.bounds.Center.X; screenY = button.bounds.Center.Y;
        }
        else return new { ok = false, error = "Action is unavailable at this carpenter-menu stage." };
        if (screenX < 0 || screenY < 0 || screenX >= Game1.uiViewport.Width || screenY >= Game1.uiViewport.Height)
            return new { ok = false, error = "Target is outside the visible menu; pan the camera first." };
        if (action == 4) CarpenterPlacement.Select(menu, x, y);
        menu.receiveLeftClick(screenX, screenY);
        return new { ok = true, note = "Clicked the carpenter menu; inspect the menu and resources for the result." };
    }

    private static object[] ExitData(GameLocation location)
    {
        var layer = location.Map?.GetLayer("Back");
        return location.warps.Select(warp => {
            int ax = layer == null ? warp.X : Math.Clamp(warp.X, 0, layer.LayerWidth - 1);
            int ay = layer == null ? warp.Y : Math.Clamp(warp.Y, 0, layer.LayerHeight - 1);
            int? direction = warp.Y < ay ? 1 : warp.X > ax ? 2 : warp.Y > ay ? 3 : warp.X < ax ? 4 : null;
            return (object)new { target = warp.TargetName, position = new[] { warp.X, warp.Y },
                target_position = new[] { warp.TargetX, warp.TargetY }, approach_position = new[] { ax, ay },
                step_direction = direction,
                instruction = direction == null ? "Move onto the warp tile; inspect the resulting location." : "Move to approach_position, then move_step(step_direction)." };
        }).ToArray();
    }

    private static IEnumerable<GameLocation> LoadedLocations()
    {
        var seen = new HashSet<GameLocation>();
        var pending = new Stack<GameLocation>(Game1.locations);
        if (Game1.currentLocation != null) pending.Push(Game1.currentLocation);
        while (pending.Count > 0)
        {
            var location = pending.Pop();
            if (!seen.Add(location)) continue;
            yield return location;
            foreach (var building in location.buildings)
                if (building.indoors.Value is GameLocation inside) pending.Push(inside);
        }
    }

    private static object WorldMap()
    {
        if (!Context.IsWorldReady || Game1.currentLocation == null)
            return new { available = false, error = "No loaded game." };
        var locations = LoadedLocations().Select(location => new
        {
            name = location.NameOrUniqueName,
            buildings = location.buildings.Select(building => new {
                type = building.buildingType.Value,
                position = new[] { building.tileX.Value, building.tileY.Value },
                human_door = building.humanDoor.Value.X < 0 ? null : new[] {
                    building.tileX.Value + building.humanDoor.Value.X,
                    building.tileY.Value + building.humanDoor.Value.Y },
                interior = building.indoors.Value?.NameOrUniqueName ?? building.nonInstancedIndoorsName.Value,
                under_construction = building.isUnderConstruction()
            }).ToArray(),
            exits = ExitData(location)
        }).ToArray();
        var layer = Game1.currentLocation.Map?.GetLayer("Back");
        return new
        {
            available = true,
            scope = "loaded_world_locations_and_warps",
            limitations = "Only currently loaded locations, buildings, and warp data; not a complete walkability map. " +
                          "Some building entrances, locked routes and dynamically generated locations may be missing.",
            current_location = Game1.currentLocation.NameOrUniqueName,
            current_map_size = layer == null ? null : new[] { layer.LayerWidth, layer.LayerHeight },
            locations
        };
    }

    public static bool Prefix(string __0, ref string __result)
    {
        string sizeS = __0;
        // Ordinary numeric requests continue through the original image API.
        var settings = new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
        if (sizeS == "clock_debug")
        {
            __result = JsonConvert.SerializeObject(new { available = Context.IsWorldReady,
                paused = Game1.paused, should_time_pass = Game1.shouldTimePass(),
                event_up = Game1.eventUp, dialogue_up = Game1.dialogueUp, freeze_controls = Game1.freezeControls,
                can_move = Game1.player?.CanMove, active_menu = Game1.activeClickableMenu?.GetType().Name,
                time = Game1.timeOfDay, location = Game1.currentLocation?.NameOrUniqueName }, settings);
            return false;
        }
        if (sizeS == "menu")
        {
            __result = JsonConvert.SerializeObject(MenuState(true), settings);
            return false;
        }
        if (sizeS == "npcs")
        {
            __result = JsonConvert.SerializeObject(NpcState(), settings);
            return false;
        }
        if (sizeS == "farm")
        {
            __result = JsonConvert.SerializeObject(FarmState(), settings);
            return false;
        }
        if (sizeS == "location_details")
        {
            __result = JsonConvert.SerializeObject(LocationDetails(), settings);
            return false;
        }
        if (sizeS == "world")
        {
            __result = JsonConvert.SerializeObject(WorldMap(), settings);
            return false;
        }
        if (sizeS == "crops")
        {
            __result = JsonConvert.SerializeObject(FarmingState(), settings);
            return false;
        }
        if (sizeS == "economy")
        {
            __result = JsonConvert.SerializeObject(EconomyState(), settings);
            return false;
        }
        if (sizeS == "machines")
        {
            __result = JsonConvert.SerializeObject(NearbyMachines(), settings);
            return false;
        }
        if (sizeS == "recipes")
        {
            __result = JsonConvert.SerializeObject(KnownRecipes(), settings);
            return false;
        }
        if (sizeS == "quests")
        {
            __result = JsonConvert.SerializeObject(QuestState(), settings);
            return false;
        }
        if (sizeS == "open_quests")
        {
            __result = JsonConvert.SerializeObject(OpenQuestLog(), settings);
            return false;
        }
        if (sizeS.StartsWith("quest_menu:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(QuestClick(sizeS), settings);
            return false;
        }
        if (sizeS == "request_sleep")
        {
            __result = JsonConvert.SerializeObject(RequestSleep(), settings);
            return false;
        }
        if (sizeS == "confirm_sleep")
        {
            __result = JsonConvert.SerializeObject(ConfirmSleep(), settings);
            return false;
        }
        if (sizeS.StartsWith("level_up_menu:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(LevelUpAction(sizeS), settings);
            return false;
        }
        if (sizeS == "entities")
        {
            __result = JsonConvert.SerializeObject(NearbyEntities(), settings);
            return false;
        }
        if (sizeS == "fishing")
        {
            __result = JsonConvert.SerializeObject(FishingState(), settings);
            return false;
        }
        if (sizeS == "fish_hook")
        {
            __result = JsonConvert.SerializeObject(FishHook(), settings);
            return false;
        }
        if (sizeS == "fish_collect")
        {
            __result = JsonConvert.SerializeObject(FishCollect(), settings);
            return false;
        }
        if (sizeS.StartsWith("animal_menu:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(AnimalMenuClick(sizeS), settings);
            return false;
        }
        if (sizeS.StartsWith("animal_pan:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(AnimalMenuPan(sizeS), settings);
            return false;
        }
        if (sizeS.StartsWith("carpenter:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(CarpenterAction(sizeS), settings);
            return false;
        }
        if (sizeS.StartsWith("pet_animal:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(PetAnimal(sizeS), settings);
            return false;
        }
        if (sizeS.StartsWith("fish_input:", StringComparison.Ordinal))
        {
            __result = JsonConvert.SerializeObject(FishingInput.Set(sizeS), settings);
            return false;
        }
        if (!sizeS.StartsWith("state:", StringComparison.Ordinal))
            return true;
        if (!int.TryParse(sizeS[6..], out int radius) || radius < 0 || radius > 64)
        {
            __result = JsonConvert.SerializeObject(new { Error = "Radius must be between 0 and 64." });
            return false;
        }
        var player = JObject.FromObject(Player.Invoke(null, null)!);
        var professionTitle = AccessTools.Method(typeof(LevelUpMenu), "getProfessionTitleFromNumber")
            ?? throw new MissingMethodException("LevelUpMenu.getProfessionTitleFromNumber");
        player["Professions"] = JArray.FromObject(Game1.player.professions.Select(id =>
            (string?)professionTitle.Invoke(null, new object[] { id }) ?? $"Unknown_{id}"));
        __result = JsonConvert.SerializeObject(new
        {
            Player = player,
            SurroundingsData = Tiles.Invoke(null, new object[] { radius }),
            Menu = MenuState(false),
            GameState = GameTime.Invoke(null, null),
            Farming = FarmingState(),
            Entities = NearbyEntities(),
            Combat = CombatStats(),
            Navigation = new { version = 2, location_id = Game1.currentLocation.NameOrUniqueName,
                pathfinding_timeout_seconds = PathfindingThreadGuard.TimeoutSeconds, cancel_on_warp = true,
                api_path_active = PathfindingThreadGuard.HasActiveRequest,
                controller_attached = Game1.player.controller != null },
            clock_rules = new { time_speed_multiplier = ClockSpeed.Multiplier },
            cutscene_rules = new { auto_skip_cutscenes = CutsceneSkip.Enabled,
                scope = "nonfestival_nonwedding_story_events", attempts = CutsceneSkip.Attempted,
                skip_handler_returns = CutsceneSkip.Skipped, errors = CutsceneSkip.Failed },
            fishing_rules = new { auto_catch = AutoFishing.Enabled, auto_hook = AutoFishing.Enabled,
                auto_collect = AutoFishing.Enabled, minigame_seconds = AutoFishing.Enabled ? (int?)10 : null }
        }, settings);
        return false;
    }
}
