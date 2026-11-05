using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace FoxBridge_WinUI;

/// <summary>The UI owns this process and its private redirected pipes.</summary>
internal sealed class BridgeClient
{
    private Process? process;
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private bool closing;
    public event Action<string, JsonElement>? Message;
    public event Action<string>? Failed;

    public void Start(string pythonPath)
    {
        var start = new ProcessStartInfo(pythonPath)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            WorkingDirectory = AppContext.BaseDirectory
        };
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add(Path.Combine(AppContext.BaseDirectory, "Backend", "worker.py"));
        start.Environment["PYTHONUTF8"] = "1";
        process = Process.Start(start) ?? throw new IOException("Не удалось запустить Python-ядро.");
        _ = Task.Run(ReadAsync);
        // Drain stderr without exposing exception text or account data to the UI.
        _ = process.StandardError.ReadToEndAsync();
    }

    private async Task ReadAsync()
    {
        try
        {
            while (await process!.StandardOutput.ReadLineAsync() is { } line)
            {
                using var document = JsonDocument.Parse(line);
                Message?.Invoke(document.RootElement.GetProperty("kind").GetString()!,
                    document.RootElement.GetProperty("data").Clone());
            }
            await process.WaitForExitAsync();
            if (!closing) Failed?.Invoke("Сетевое ядро завершилось. Проверьте целостность папки Backend и перезапустите FoxBridge.");
        }
        catch (Exception) { if (!closing) Failed?.Invoke("Связь с сетевым ядром потеряна. Проверьте целостность папки Backend и перезапустите FoxBridge."); }
    }

    public async Task Send(string operation, object? args = null)
    {
        await writeLock.WaitAsync();
        try
        {
            if (process is null || process.HasExited) throw new IOException("Сетевое ядро недоступно.");
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { operation, args }));
            await process.StandardInput.FlushAsync();
        }
        finally { writeLock.Release(); }
    }

    public async Task Stop()
    {
        closing = true;
        if (process is null || process.HasExited) return;
        try
        {
            await Send("shutdown");
            process.StandardInput.Close();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(25));
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (Exception)
        {
            // Kill only the child owned by this window, never global CLI daemons.
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        finally { process.Dispose(); }
    }
}
