using System.Diagnostics;
using System.Text.Json;

namespace FoxBridge_WinUI;

internal sealed record DependencyReport(string? PythonPath, string Summary, string? Problem);

internal static class DependencyCheck
{
    internal static readonly string[] RequiredFiles = ["worker.py", "bridge.py", "netcoffee.py", "launcher.py", "timezones.py", "cacert.pem"];

    public static async Task<DependencyReport> Run(string baseDirectory)
    {
        var summary = $".NET {Environment.Version} · загружен\nWindows App Runtime · загружен";
        var missing = RequiredFiles.Where(name => !File.Exists(Path.Combine(baseDirectory, "Backend", name))).ToArray();
        if (missing.Length > 0)
            return new(null, summary, "Неполная папка приложения. Восстановите рабочую сборку целиком. Отсутствуют: " + string.Join(", ", missing));

        string? oldVersion = null;
        foreach (var candidate in Candidates())
        {
            using var process = new Process { StartInfo = new(candidate)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            } };
            if (Path.GetFileNameWithoutExtension(candidate).Equals("py", StringComparison.OrdinalIgnoreCase))
                process.StartInfo.ArgumentList.Add("-3");
            foreach (var arg in new[] { "-I", "-c", "import sys,json; print(json.dumps({'version':list(sys.version_info[:3]),'executable':sys.executable}))" })
                process.StartInfo.ArgumentList.Add(arg);
            try
            {
                if (!process.Start()) continue;
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                var output = process.StandardOutput.ReadToEndAsync(timeout.Token);
                var error = process.StandardError.ReadToEndAsync(timeout.Token);
                await process.WaitForExitAsync(timeout.Token);
                var text = await output;
                await error;
                if (process.ExitCode != 0 || text.Length > 16384) continue;
                using var data = JsonDocument.Parse(text);
                var parts = data.RootElement.GetProperty("version").EnumerateArray().Select(x => x.GetInt32()).ToArray();
                var version = new Version(parts[0], parts[1], parts[2]);
                var executable = data.RootElement.GetProperty("executable").GetString();
                if (version < new Version(3, 12)) { oldVersion = version.ToString(); continue; }
                if (string.IsNullOrEmpty(executable) || !File.Exists(executable)) continue;
                return new(executable, summary + $"\nPython {version} · готов", null);
            }
            catch (Exception error) when (error is not OutOfMemoryException)
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch (Exception) { }
            }
        }
        return new(null, summary + "\nPython · требуется установка", oldVersion is null
            ? "Python 3.12 или новее не найден в PATH. Установите Python с python.org, включите добавление в PATH и перезапустите FoxBridge."
            : $"Найден Python {oldVersion}. Нужна версия 3.12 или новее. Установите её с python.org и перезапустите FoxBridge.");
    }

    private static IEnumerable<string> Candidates()
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in new[] { "python.exe", "python3.exe", "py.exe" })
            foreach (var folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            {
                string path;
                try { path = Path.GetFullPath(Path.Combine(folder.Trim().Trim('"'), name)); }
                catch (Exception) { continue; }
                // Store execution aliases can open an installation UI instead of running Python.
                if (name != "py.exe" && path.Contains("\\Microsoft\\WindowsApps\\", StringComparison.OrdinalIgnoreCase)) continue;
                if (File.Exists(path) && seen.Add(path)) yield return path;
            }
    }
}
