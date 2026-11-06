using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

namespace FoxBridge_WinUI;

// A normal Firefox instance. No WebDriver, debugging port or JS emulation.
internal sealed class FirefoxSession : IAsyncDisposable
{
    private readonly FileStream profileLease;
    private readonly string executable;
    private readonly Dictionary<int, DateTime> ownedProcesses = [];
    private Process? process;
    private Task? restoreTask;
    private bool preferencesApplied, started;
    public int Port { get; }
    public string ProfilePath { get; }
    public bool IsOpen => started && ProfileIsLocked();

    private FirefoxSession(int port, string profile, string executable, FileStream lease)
    {
        Port = port; ProfilePath = profile; this.executable = executable; profileLease = lease;
    }

    public static async Task<FirefoxSession> Start(int port, string profilePath, CancellationToken cancellation)
    {
        if (port is < 1024 or > 65535) throw new InvalidOperationException("Некорректный порт прокси.");
        var profile = Path.GetFullPath(profilePath);
        if (!Directory.Exists(profile)) throw new InvalidOperationException("Выбранный профиль Firefox не найден. Загрузите профиль VPN заново.");
        var executable = FindExecutable();
        FileStream lease;
        try { lease = new FileStream(Path.Combine(profile, "foxbridge.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { throw new InvalidOperationException("Этот профиль Firefox уже используется другим экземпляром FoxBridge."); }
        var browser = new FirefoxSession(port, profile, executable, lease);
        try
        {
            if (browser.ProfileIsLocked())
                throw new InvalidOperationException("Выбранный VPN-профиль уже используется. Закройте все его окна Firefox и повторите запуск.");
            browser.preferencesApplied = true;
            FirefoxProfilePreferences.Apply(profile, new Dictionary<string, object>
            {
                ["network.proxy.type"] = 1,
                ["network.proxy.http"] = "127.0.0.1", ["network.proxy.http_port"] = port,
                ["network.proxy.ssl"] = "127.0.0.1", ["network.proxy.ssl_port"] = port,
                ["network.proxy.share_proxy_settings"] = true,
                ["network.proxy.no_proxies_on"] = "localhost,127.0.0.1,::1",
                ["network.proxy.failover_direct"] = false,
                ["network.trr.mode"] = 5,
                ["network.dns.disablePrefetch"] = true,
                ["network.prefetch-next"] = false,
                ["network.predictor.enabled"] = false,
                ["network.http.speculative-parallel-limit"] = 0,
                ["network.http.http3.enable"] = false,
                ["media.peerconnection.enabled"] = false,
                ["browser.startup.page"] = 3,
            });
            var start = browser.StartInfo();
            foreach (var argument in new[] { "--new-instance", "--profile", profile, "--wait-for-browser" })
                start.ArgumentList.Add(argument);
            browser.process = Process.Start(start) ?? throw new IOException("Не удалось запустить Firefox.");
            browser.RememberProcess(browser.process.Id);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));
            while (!browser.ProfileIsLocked())
            {
                timeout.Token.ThrowIfCancellationRequested();
                if (browser.process.HasExited) throw new InvalidOperationException("Firefox завершился до открытия профиля. Закройте его окна и повторите запуск.");
                await Task.Delay(150, timeout.Token);
            }
            browser.started = true;
            browser.RememberDescendants();
            browser.restoreTask = browser.RestoreAfterExit();
            return browser;
        }
        catch { await browser.DisposeAsync(); throw; }
    }

    private ProcessStartInfo StartInfo()
    {
        var start = new ProcessStartInfo(executable) { UseShellExecute = false };
        // Firefox uses its normal profile/system locale and timezone.
        start.Environment.Remove("TZ");
        start.Environment.Remove("MOZ_NO_REMOTE");
        return start;
    }

    public Task OpenTab(CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        var start = StartInfo();
        foreach (var argument in new[] { "--profile", ProfilePath, "--new-tab", "about:blank" }) start.ArgumentList.Add(argument);
        using var request = Process.Start(start) ?? throw new IOException("Не удалось открыть вкладку Firefox.");
        return Task.CompletedTask;
    }

    private bool ProfileIsLocked()
    {
        var path = Path.Combine(ProfilePath, "parent.lock");
        if (!File.Exists(path)) return false;
        try { using var handle = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None); return false; }
        catch (IOException) { return true; }
        catch (UnauthorizedAccessException) { return true; }
    }

    private async Task RestoreAfterExit()
    {
        while (ProfileIsLocked()) await Task.Delay(300).ConfigureAwait(false);
        await RestoreWhenUnlocked().ConfigureAwait(false);
    }

    private async Task RestoreWhenUnlocked()
    {
        if (!preferencesApplied) return;
        for (var attempt = 0; attempt < 40; attempt++)
        {
            try
            {
                using var parentLock = new FileStream(Path.Combine(ProfilePath, "parent.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
                FirefoxProfilePreferences.Restore(ProfilePath);
                preferencesApplied = false;
                return;
            }
            catch (IOException) { await Task.Delay(150).ConfigureAwait(false); }
        }
        // Keep the recovery journal if Firefox is still using the profile.
    }

    private void RememberProcess(int pid)
    {
        try { using var candidate = Process.GetProcessById(pid); ownedProcesses[pid] = candidate.StartTime; }
        catch (ArgumentException) { }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
    }

    private bool IsOriginalProcess(int pid)
    {
        try { using var candidate = Process.GetProcessById(pid); return ownedProcesses.TryGetValue(pid, out var created) && candidate.StartTime == created; }
        catch (ArgumentException) { return false; }
        catch (InvalidOperationException) { return false; }
        catch (System.ComponentModel.Win32Exception) { return false; }
    }

    private void RememberDescendants()
    {
        if (process is null || !IsOriginalProcess(process.Id)) return;
        var snapshot = CreateToolhelp32Snapshot(2, 0);
        if (snapshot == new IntPtr(-1)) return;
        try
        {
            var entries = new List<(int Id, int Parent)>();
            var entry = new ProcessEntry { Size = (uint)Marshal.SizeOf<ProcessEntry>() };
            if (Process32First(snapshot, ref entry))
                do
                {
                    if (string.Equals(entry.Executable, "firefox.exe", StringComparison.OrdinalIgnoreCase))
                        entries.Add(((int)entry.Id, (int)entry.ParentId));
                } while (Process32Next(snapshot, ref entry));
            var family = ownedProcesses.Keys.Where(IsOriginalProcess).ToHashSet();
            bool changed;
            do
            {
                changed = false;
                foreach (var child in entries)
                    if (family.Contains(child.Parent) && family.Add(child.Id)) { RememberProcess(child.Id); changed = true; }
            } while (changed);
        }
        finally { CloseHandle(snapshot); }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ProcessEntry
    {
        public uint Size, Usage, Id;
        public IntPtr DefaultHeap;
        public uint ModuleId, Threads, ParentId;
        public int Priority;
        public uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string Executable;
    }
    [DllImport("kernel32.dll")] private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
    [DllImport("kernel32.dll", EntryPoint = "Process32FirstW", CharSet = CharSet.Unicode)] private static extern bool Process32First(IntPtr snapshot, ref ProcessEntry entry);
    [DllImport("kernel32.dll", EntryPoint = "Process32NextW", CharSet = CharSet.Unicode)] private static extern bool Process32Next(IntPtr snapshot, ref ProcessEntry entry);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    private delegate bool WindowCallback(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder name, int capacity);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    private void CloseOwnedWindows()
    {
        RememberDescendants();
        var owners = ownedProcesses.Keys.Where(IsOriginalProcess).ToHashSet();
        EnumWindows((window, _) =>
        {
            GetWindowThreadProcessId(window, out var owner);
            if (owners.Contains((int)owner) && IsWindowVisible(window))
            {
                var name = new StringBuilder(128);
                GetClassName(window, name, name.Capacity);
                if (name.ToString() == "MozillaWindowClass") PostMessage(window, 0x0010, IntPtr.Zero, IntPtr.Zero);
            }
            return true;
        }, IntPtr.Zero);
    }

    public async ValueTask DisposeAsync()
    {
        try
        {
            CloseOwnedWindows();
            if (restoreTask is not null)
            {
                try { await restoreTask.WaitAsync(TimeSpan.FromSeconds(7)); }
                catch (TimeoutException) { } // Never force-kill a user's browser.
            }
            else await RestoreWhenUnlocked();
        }
        catch (Exception error) when (error is IOException or OperationCanceledException or InvalidOperationException) { }
        finally { process?.Dispose(); profileLease.Dispose(); }
    }

    private static string FindExecutable()
    {
        var paths = new List<string>();
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            using var root = RegistryKey.OpenBaseKey(hive, view);
            using var key = root.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe");
            if (key?.GetValue(null) is string path) paths.Add(path.Trim('"'));
        }
        foreach (var folder in new[] { Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.LocalApplicationData })
            paths.Add(Path.Combine(Environment.GetFolderPath(folder), "Mozilla Firefox", "firefox.exe"));
        return paths.FirstOrDefault(File.Exists) ?? throw new InvalidOperationException("Firefox не найден. Установите Firefox в стандартную папку и повторите запуск.");
    }

}
