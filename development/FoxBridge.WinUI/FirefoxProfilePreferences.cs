using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace FoxBridge_WinUI;

// Preserve user.js and restore only the preferences changed for this launch.
// The journal also allows recovery after FoxBridge or Firefox crashes.
internal static class FirefoxProfilePreferences
{
    private const string JournalName = "foxbridge-preferences.json";
    private static readonly Regex Preference = new("^[ \\t]*user_pref\\((?<key>\"(?:\\\\.|[^\"\\\\])*\")\\s*,\\s*(?<value>.+)\\);[ \\t]*\\r?$", RegexOptions.Multiline);

    private sealed record Journal(byte[]? UserJs, byte[] ManagedBlock, Dictionary<string, JsonElement?> Values);

    public static void Apply(string profile, IReadOnlyDictionary<string, object> preferences)
    {
        Restore(profile);
        var userPath = Path.Combine(profile, "user.js");
        var originalUser = File.Exists(userPath) ? File.ReadAllBytes(userPath) : null;
        var prefsPath = Path.Combine(profile, "prefs.js");
        var originalPrefs = File.Exists(prefsPath) ? File.ReadAllText(prefsPath) : "";
        var values = preferences.Keys.ToDictionary(key => key, _ => (JsonElement?)null, StringComparer.Ordinal);
        foreach (Match match in Preference.Matches(originalPrefs))
        {
            var key = JsonSerializer.Deserialize<string>(match.Groups["key"].Value)!;
            if (values.ContainsKey(key))
                values[key] = JsonSerializer.Deserialize<JsonElement>(match.Groups["value"].Value);
        }
        var block = Encoding.UTF8.GetBytes("\n// FoxBridge session settings begin\n" +
            string.Join("\n", preferences.Select(p => $"user_pref({JsonSerializer.Serialize(p.Key)}, {JsonSerializer.Serialize(p.Value)});")) +
            "\n// FoxBridge session settings end\n");
        var journal = new Journal(originalUser, block, values);
        // Commit recovery data before touching Firefox's preferences.
        WriteAtomic(Path.Combine(profile, JournalName), JsonSerializer.SerializeToUtf8Bytes(journal));
        WriteAtomic(userPath, [.. originalUser ?? [], .. block]);
    }

    public static void Restore(string profile)
    {
        var journalPath = Path.Combine(profile, JournalName);
        if (!File.Exists(journalPath)) return;
        var journal = JsonSerializer.Deserialize<Journal>(File.ReadAllBytes(journalPath))
            ?? throw new InvalidOperationException("Не удалось прочитать сохранённые настройки Firefox. Файл foxbridge-preferences.json оставлен в профиле.");
        var prefsPath = Path.Combine(profile, "prefs.js");
        if (File.Exists(prefsPath))
        {
            var current = File.ReadAllText(prefsPath);
            var restored = Preference.Replace(current, match =>
            {
                var key = JsonSerializer.Deserialize<string>(match.Groups["key"].Value)!;
                return journal.Values.ContainsKey(key) ? "" : match.Value;
            });
            foreach (var (key, value) in journal.Values)
                if (value is { } original)
                    restored += $"\nuser_pref({JsonSerializer.Serialize(key)}, {original.GetRawText()});";
            WriteAtomic(prefsPath, Encoding.UTF8.GetBytes(restored));
        }
        var userPath = Path.Combine(profile, "user.js");
        if (File.Exists(userPath))
        {
            var current = File.ReadAllBytes(userPath);
            var index = current.AsSpan().IndexOf(journal.ManagedBlock.AsSpan());
            if (index >= 0)
            {
                byte[] restored = [.. current.AsSpan(0, index), .. current.AsSpan(index + journal.ManagedBlock.Length)];
                if (restored.Length == 0 && journal.UserJs is null) File.Delete(userPath);
                else WriteAtomic(userPath, restored);
            }
            else if (!current.AsSpan().SequenceEqual((journal.UserJs ?? []).AsSpan()))
                throw new InvalidOperationException("user.js изменён во время работы Firefox. Исходные настройки сохранены в foxbridge-preferences.json; автоматическое восстановление остановлено.");
        }
        File.Delete(journalPath);
    }

    private static void WriteAtomic(string path, byte[] data)
    {
        var temporary = path + ".foxbridge.tmp";
        using (var stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            stream.Write(data);
            stream.Flush(flushToDisk: true);
        }
        File.Move(temporary, path, overwrite: true);
    }
}
