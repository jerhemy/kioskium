// Minimal C# client for kioskium (no NuGet packages needed; .NET 6+).
//
// With the daemon (`kioskctl serve`, e.g. as a service): the application only talks to the daemon. It
// starts and closes browsers by name and reaches each browser through it; the browsers' own ports and
// tokens never matter, and a browser the daemon restarted keeps its name.
//
//   using var kiosk = new KioskiumDaemon("http://127.0.0.1:7000", token);   // token: serve --token, or null
//   if (!await kiosk.HealthAsync()) { /* the service is not running */ }
//   await kiosk.OpenAsync(name: "info", appId: "infotainment-screen", url: "http://localhost:8080/",
//       options: new KioskOptions { ShowCursorTimeout = 3 });
//   var info = kiosk.Instance("info");                                       // a KioskiumClient
//   await info.NavigateAsync("http://localhost:8080/route");
//   var title = await info.JsAsync("document.title");
//   await foreach (var (name, data) in info.EventsAsync(ct)) { ... }         // that browser's events
//   await foreach (var (name, data) in kiosk.EventsAsync(ct)) { ... }        // started, ready, exited, restarting, ...
//   await kiosk.RestartAsync("info");
//   await kiosk.CloseAsync("info");                                          // stops it; the daemon forgets it
//
// Without the daemon: one browser started with `kiosk --control-port 7001`:
//
//   using var driver = new KioskiumClient(7001);
//   await driver.NavigateAsync("http://localhost:8080/");
//
// Failed requests throw KioskiumException with the HTTP status and the JSON reply, e.g. 409 from
// OpenAsync when that name is already running, or from any browser call while it is not running.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

public sealed class KioskiumException : Exception
{
    public HttpStatusCode StatusCode { get; }
    public JsonElement Reply { get; }

    public KioskiumException(string what, HttpStatusCode status, JsonElement reply)
        : base($"kioskium {what}: {(int)status} {reply}")
    {
        StatusCode = status;
        Reply = reply;
    }
}

/// <summary>
/// Options for a browser the daemon starts (docs/OPTIONS.md has what each does). Unset properties are
/// left out, so the browser's defaults and kioskium.json apply. Options that touch files or Chromium
/// internals (--chromium-flag, --inject-js, --log-file, ...) are refused unless the daemon runs with
/// --allow-unsafe-args; pass those in Args. Name, app-id and the control channel are set by the daemon.
/// </summary>
public sealed class KioskOptions
{
    // Window
    public bool Windowed { get; set; }
    /// <summary>"WxH" or "WxH+X+Y".</summary>
    public string? Size { get; set; }
    /// <summary>Index from --list-screens, or a (partial) screen name such as "HDMI-A-1".</summary>
    public string? Screen { get; set; }
    /// <summary>Show the pointer (kiosk mode hides it by default).</summary>
    public bool ShowCursor { get; set; }
    /// <summary>Show the pointer while a mouse moves, hide it again after this many idle seconds.</summary>
    public double? ShowCursorTimeout { get; set; }
    /// <summary>e.g. "#000000".</summary>
    public string? Background { get; set; }

    // Split
    public string? Split { get; set; }
    /// <summary>"horizontal" or "vertical".</summary>
    public string? SplitOrientation { get; set; }
    public int? SplitRatio { get; set; }
    public int? SplitGap { get; set; }

    // Page
    public string? UserAgent { get; set; }
    public int? CacheSize { get; set; }
    /// <summary>Hosts the page may navigate to (fnmatch globs).</summary>
    public IList<string> AllowHosts { get; set; } = new List<string>();
    public bool TextSelection { get; set; }
    public bool ContextMenu { get; set; }
    public bool AllowNewWindows { get; set; }
    public bool AllowDialogs { get; set; }
    public bool GrantPermissions { get; set; }
    public bool ShowScrollbars { get; set; }
    public bool IgnoreCertificateErrors { get; set; }
    public bool NoJsApi { get; set; }

    // Touch
    /// <summary>Turn touch events off (they are on by default).</summary>
    public bool NoTouch { get; set; }

    // Graphics
    public string? Platform { get; set; }
    /// <summary>"auto", "es" or "desktop".</summary>
    public string? OpenGl { get; set; }
    public bool NoGpu { get; set; }
    /// <summary>Draw page tiles on the GPU (off by default; black/flickering tiles on small GPU memory pools such as i.MX).</summary>
    public bool GpuRasterization { get; set; }
    public bool NoRgbaSurface { get; set; }
    /// <summary>Enable Chromium's renderer sandbox (off by default; it refuses to run as root).</summary>
    public bool Sandbox { get; set; }

    // Loading and recovery
    public string? LoadingTitle { get; set; }
    public string? LoadingDetail { get; set; }
    public bool LoadingStatus { get; set; }
    public bool NoReloadOnCrash { get; set; }
    public double? RetryInterval { get; set; }
    public double? ReloadInterval { get; set; }
    /// <summary>Retry a load slower than this (default 30 s; 0 = never).</summary>
    public double? LoadTimeout { get; set; }

    // Keys and diagnostics
    public string? ExitShortcut { get; set; }
    public bool AllowShortcuts { get; set; }
    public bool DevTools { get; set; }
    public bool DevToolsOpen { get; set; }
    public string? DevToolsKey { get; set; }
    public bool Fps { get; set; }
    /// <summary>"top-left", "top-right", "bottom-left" or "bottom-right".</summary>
    public string? FpsCorner { get; set; }
    /// <summary>"debug", "info", "warning" or "error".</summary>
    public string? LogLevel { get; set; }
    public bool PageConsole { get; set; }

    /// <summary>Anything else, appended as given: new List&lt;string&gt; { "--chromium-flag", "--foo" }.</summary>
    public IList<string> Args { get; set; } = new List<string>();

    /// <summary>The command-line options these settings stand for.</summary>
    public List<string> ToArgs()
    {
        var a = new List<string>();
        void Flag(string option, bool on) { if (on) a.Add(option); }
        void Value(string option, object? value)
        {
            if (value == null) return;
            a.Add(option);
            a.Add(Convert.ToString(value, CultureInfo.InvariantCulture)!);
        }

        Flag("--windowed", Windowed);
        Value("--size", Size);
        Value("--screen", Screen);
        Flag("--show-cursor", ShowCursor);
        Value("--show-cursor-timeout", ShowCursorTimeout);
        Value("--background", Background);
        Value("--split", Split);
        Value("--split-orientation", SplitOrientation);
        Value("--split-ratio", SplitRatio);
        Value("--split-gap", SplitGap);
        Value("--user-agent", UserAgent);
        Value("--cache-size", CacheSize);
        foreach (var host in AllowHosts) Value("--allow-host", host);
        Flag("--text-selection", TextSelection);
        Flag("--context-menu", ContextMenu);
        Flag("--allow-new-windows", AllowNewWindows);
        Flag("--allow-dialogs", AllowDialogs);
        Flag("--grant-permissions", GrantPermissions);
        Flag("--show-scrollbars", ShowScrollbars);
        Flag("--ignore-certificate-errors", IgnoreCertificateErrors);
        Flag("--no-js-api", NoJsApi);
        Flag("--no-touch", NoTouch);
        Value("--platform", Platform);
        Value("--opengl", OpenGl);
        Flag("--no-gpu", NoGpu);
        Flag("--gpu-rasterization", GpuRasterization);
        Flag("--no-rgba-surface", NoRgbaSurface);
        Flag("--sandbox", Sandbox);
        Value("--loading-title", LoadingTitle);
        Value("--loading-detail", LoadingDetail);
        Flag("--loading-status", LoadingStatus);
        Flag("--no-reload-on-crash", NoReloadOnCrash);
        Value("--retry-interval", RetryInterval);
        Value("--reload-interval", ReloadInterval);
        Value("--load-timeout", LoadTimeout);
        Value("--exit-shortcut", ExitShortcut);
        Flag("--allow-shortcuts", AllowShortcuts);
        Flag("--devtools", DevTools);
        Flag("--devtools-open", DevToolsOpen);
        Value("--devtools-key", DevToolsKey);
        Flag("--fps", Fps);
        Value("--fps-corner", FpsCorner);
        Value("--log-level", LogLevel);
        Flag("--page-console", PageConsole);
        a.AddRange(Args);
        return a;
    }
}

/// <summary>The daemon (`kioskctl serve`): starts, restarts and closes browsers and forwards their API.</summary>
public sealed class KioskiumDaemon : IDisposable
{
    private readonly Http _http;

    public KioskiumDaemon(string baseUrl = "http://127.0.0.1:7000", string? token = null) =>
        _http = new Http(baseUrl.TrimEnd('/') + "/api", token);

    /// <summary>True if the daemon answers (GET /api/health needs no token).</summary>
    public async Task<bool> HealthAsync(CancellationToken ct = default)
    {
        try
        {
            var h = await _http.SendAsync(HttpMethod.Get, "health", null, ct);
            return h.TryGetProperty("daemon", out var d) && d.GetBoolean();
        }
        catch (Exception e) when (e is HttpRequestException or KioskiumException or TaskCanceledException) { return false; }
    }

    /// <summary>{ok, daemon, version, pid, instances: [names]}</summary>
    public Task<JsonElement> StatusAsync(CancellationToken ct = default) => _http.SendAsync(HttpMethod.Get, "status", null, ct);

    /// <summary>Every instance the daemon manages: name, appId, state, pid, url, args, restart, restarts, exitCode, startedAt.</summary>
    public async Task<JsonElement> ListAsync(CancellationToken ct = default) =>
        (await _http.SendAsync(HttpMethod.Get, "instances", null, ct)).GetProperty("instances");

    /// <summary>{instance, status}: the instance, plus the browser's live /api/status while it runs.</summary>
    public Task<JsonElement> GetAsync(string name, CancellationToken ct = default) =>
        _http.SendAsync(HttpMethod.Get, Path(name), null, ct);

    /// <summary>
    /// Start a browser and return its instance. name defaults to appId; options are the kiosk options
    /// (new KioskOptions { Windowed = true, Size = "1280x800" }); restart is "on-failure", "always" (also
    /// relaunch a closed window) or "no"; default: the daemon's (serve --restart). The call waits up to <paramref name="wait"/> seconds (default 60) for the page; state is
    /// "running", or "starting" if it is still coming up.
    /// </summary>
    public async Task<JsonElement> OpenAsync(string? name = null, string? appId = null, string? url = null,
        KioskOptions? options = null, string? restart = null, double? wait = null, CancellationToken ct = default)
    {
        var body = new Dictionary<string, object>();
        if (name != null) body["name"] = name;
        if (appId != null) body["appId"] = appId;
        if (url != null) body["url"] = url;
        if (options != null) body["args"] = options.ToArgs();
        if (restart != null) body["restart"] = restart;
        if (wait != null) body["wait"] = wait;
        var reply = await _http.SendAsync(HttpMethod.Post, "instances", body, ct, LongCall(wait));
        return reply.GetProperty("instance");
    }

    /// <summary>Stop and start again with the same settings.</summary>
    public async Task<JsonElement> RestartAsync(string name, CancellationToken ct = default) =>
        (await _http.SendAsync(HttpMethod.Post, Path(name) + "/restart", new { }, ct, LongCall(null))).GetProperty("instance");

    /// <summary>Stop the browser and remove it from the daemon's list.</summary>
    public Task CloseAsync(string name, CancellationToken ct = default) =>
        _http.SendAsync(HttpMethod.Delete, Path(name), null, ct, LongCall(null));

    /// <summary>Stop the browser but keep it listed as "stopped", so StartAsync can bring it back.</summary>
    public Task StopAsync(string name, CancellationToken ct = default) =>
        _http.SendAsync(HttpMethod.Post, Path(name) + "/stop", new { }, ct, LongCall(null));

    public async Task<JsonElement> StartAsync(string name, CancellationToken ct = default) =>
        (await _http.SendAsync(HttpMethod.Post, Path(name) + "/start", new { }, ct, LongCall(null))).GetProperty("instance");

    /// <summary>A client for one browser, through the daemon (/api/instances/NAME/...).</summary>
    public KioskiumClient Instance(string name) => new KioskiumClient(_http.Under(Path(name)));

    /// <summary>The daemon's events: started, ready, exited, restarting, stopped, failed (data has the name).</summary>
    public IAsyncEnumerable<(string Name, JsonElement Data)> EventsAsync(CancellationToken ct = default, long since = 0) =>
        _http.EventsAsync($"events?since={since}", ct);

    public void Dispose() => _http.Dispose();

    private static string Path(string name) => "instances/" + Uri.EscapeDataString(name);

    // Starting a browser can take the whole wait (default 60 s), stopping one up to 10 s.
    private static TimeSpan LongCall(double? wait) => TimeSpan.FromSeconds((wait ?? 60) + 15);
}

/// <summary>One browser: through the daemon (KioskiumDaemon.Instance) or on its own control port.</summary>
public sealed class KioskiumClient : IDisposable
{
    private readonly Http _http;

    /// <summary>A browser started with --control-port PORT (and --control-token TOKEN).</summary>
    public KioskiumClient(int port, string? token = null) : this(new Http($"http://127.0.0.1:{port}/api", token)) { }

    internal KioskiumClient(Http http) => _http = http;

    /// <summary>True if the browser answers. Through the daemon, false while it is not running.</summary>
    public async Task<bool> IsRunningAsync(CancellationToken ct = default)
    {
        try
        {
            var h = await _http.SendAsync(HttpMethod.Get, "health", null, ct);
            return h.TryGetProperty("ok", out var ok) && ok.GetBoolean();
        }
        catch (Exception e) when (e is HttpRequestException or KioskiumException or TaskCanceledException) { return false; }
    }

    public Task<JsonElement> StatusAsync(CancellationToken ct = default) => _http.SendAsync(HttpMethod.Get, "status", null, ct);

    public Task NavigateAsync(string url, CancellationToken ct = default) => Post("navigate", new { url }, ct);
    public Task ReloadAsync(CancellationToken ct = default) => Post("reload", new { }, ct);
    public Task SplitAsync(string url, string orientation = "horizontal", int ratio = 50, CancellationToken ct = default) =>
        Post("split", new { url, orientation, ratio }, ct);
    public Task UnsplitAsync(CancellationToken ct = default) => Post("unsplit", new { }, ct);
    public Task DevToolsAsync(string action = "toggle", CancellationToken ct = default) => Post("devtools", new { action }, ct);
    public Task FpsAsync(bool enabled, CancellationToken ct = default) => Post("fps", new { enabled }, ct);
    /// <summary>Save the window as PNG; the path is on the browser's machine.</summary>
    public Task ScreenshotAsync(string path, CancellationToken ct = default) => Post("screenshot", new { path }, ct);
    /// <summary>Quit the browser. Through the daemon, prefer KioskiumDaemon.CloseAsync (or StopAsync).</summary>
    public Task QuitAsync(CancellationToken ct = default) => Post("quit", new { }, ct);

    public async Task<JsonElement> JsAsync(string code, CancellationToken ct = default) =>
        (await Post("js", new { code }, ct)).GetProperty("result");

    /// <summary>Server-Sent Events: load, navigate, crash, split, unsplit, devtools, fps, quit.</summary>
    public IAsyncEnumerable<(string Name, JsonElement Data)> EventsAsync(CancellationToken ct = default, long since = 0) =>
        _http.EventsAsync($"events?since={since}", ct);

    public void Dispose() => _http.Dispose();

    private Task<JsonElement> Post(string endpoint, object body, CancellationToken ct) =>
        _http.SendAsync(HttpMethod.Post, endpoint, body, ct);
}

/// <summary>The HTTP plumbing both classes share. One HttpClient per daemon, shared with its instances.</summary>
internal sealed class Http : IDisposable
{
    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(10);
    private readonly HttpClient _client;
    private readonly string _base;
    private readonly bool _owner;

    public Http(string baseUrl, string? token)
    {
        // No client-wide timeout: event streams stay open. Each request gets its own below.
        _client = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        if (token != null) _client.DefaultRequestHeaders.Add("X-Kioskium-Token", token);
        _base = baseUrl;
        _owner = true;
    }

    private Http(HttpClient client, string baseUrl)
    {
        _client = client;
        _base = baseUrl;
    }

    public Http Under(string path) => new Http(_client, $"{_base}/{path}");

    public async Task<JsonElement> SendAsync(HttpMethod method, string endpoint, object? body, CancellationToken ct,
        TimeSpan? timeout = null)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout ?? DefaultTimeout);
        using var request = new HttpRequestMessage(method, $"{_base}/{endpoint}");
        // A string body, not JsonContent: kioskium needs Content-Length and does not read chunked bodies.
        if (body != null) request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        using var resp = await _client.SendAsync(request, cts.Token);
        var text = await resp.Content.ReadAsStringAsync(cts.Token);
        JsonElement json;
        try { json = JsonDocument.Parse(text.Length == 0 ? "{}" : text).RootElement; }
        catch (JsonException) { json = JsonSerializer.SerializeToElement(new { ok = false, error = text }); }
        if (!resp.IsSuccessStatusCode)
            throw new KioskiumException($"{method} {endpoint}", resp.StatusCode, json);
        return json;
    }

    public async IAsyncEnumerable<(string Name, JsonElement Data)> EventsAsync(string endpoint,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, $"{_base}/{endpoint}");
        using var resp = await _client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var text = await resp.Content.ReadAsStringAsync(ct);
            throw new KioskiumException($"GET {endpoint}", resp.StatusCode,
                JsonSerializer.SerializeToElement(new { ok = false, error = text }));
        }
        using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream);
        string? name = null, data = null;
        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync();
            if (line == null) yield break;                      // the stream ended (browser quit, daemon stopped)
            if (line.StartsWith("event:")) name = line[6..].Trim();
            else if (line.StartsWith("data:")) data = line[5..].Trim();
            else if (line.Length == 0 && name != null)
            {
                yield return (name, JsonDocument.Parse(data ?? "{}").RootElement);
                name = null; data = null;
            }
            // lines starting with ':' are heartbeats; 'id:' carries the sequence number for ?since=
        }
    }

    public void Dispose()
    {
        if (_owner) _client.Dispose();
    }
}
