using System.Text.Json;
using System.Text.Json.Serialization;

namespace FoxBridge_WinUI;

public sealed record SavedServer(string Host, int Port);

public sealed class UiPreferences
{
    [JsonPropertyName("profiles_ini")] public string ProfilesIni { get; set; } = "";
    [JsonPropertyName("profile_path")] public string ProfilePath { get; set; } = "";
    [JsonPropertyName("port")] public int Port { get; set; } = 8765;
    [JsonPropertyName("servers")] public Dictionary<string, SavedServer> Servers { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    public static UiPreferences Load(string path)
    {
        var value = File.Exists(path)
            ? JsonSerializer.Deserialize<UiPreferences>(File.ReadAllText(path)) ?? new() : new();
        if (value.Port is < 1024 or > 65535) value.Port = 8765;
        value.ProfilesIni ??= "";
        value.ProfilePath ??= "";
        value.Servers = new(value.Servers ?? [], StringComparer.OrdinalIgnoreCase);
        return value;
    }

    public int? FindServer(string profilePath, IReadOnlyList<SavedServer> available)
    {
        if (!Servers.TryGetValue(profilePath, out var saved)) return null;
        for (var index = 0; index < available.Count; index++)
            if (available[index].Port == saved.Port && string.Equals(available[index].Host, saved.Host, StringComparison.OrdinalIgnoreCase))
                return index;
        return -1;
    }

    public void Save(string path)
    {
        var temporary = path + ".tmp";
        try
        {
            File.WriteAllText(temporary, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temporary, path, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
