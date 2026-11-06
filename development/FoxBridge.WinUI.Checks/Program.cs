using System.Text.Json;
using FoxBridge_WinUI;

static IReadOnlyList<StatisticLine> Rows(string json)
{
    using var doc = JsonDocument.Parse(json);
    return StatisticsFormatter.Rows(doc.RootElement);
}
static void Equal(string expected, string actual)
{
    if (expected != actual) throw new Exception($"Expected '{expected}', got '{actual}'");
}
static string Find(IReadOnlyList<StatisticLine> rows, string label) => rows.Single(r => r.Label == label).Value;

var empty = Rows("{}");
Equal("Нет данных", Find(empty, "Область оценки"));
Equal("Нет данных", Find(empty, "VPN (по базе)"));

var sample = Rows("""
{
  "score": 0, "region_adjusted": false, "score_scope": "cidr",
  "trace": {"loc": "DE"}, "geoip": {"country":"US", "city":"Los Angeles"},
  "iprisk": {"country":"Germany", "is_vpn": false, "related_domains":["one.test", "two.test"],
             "intelligence":{"threats":{"malware":false,"spam":true}}},
  "timezone_candidate":"America/Los_Angeles", "timezone_estimated":true,
  "errors":{"geoip":"Время ожидания истекло"}
}
""");
Equal("0", Find(sample, "Score Net.Coffee"));
Equal("Нет", Find(sample, "VPN (по базе)"));
Equal("DE", Find(sample, "Страна по Claude trace"));
Equal("US", Find(sample, "Страна · GeoIP"));
Equal("Germany", Find(sample, "Страна · Risk API"));
Equal("Подсеть CIDR · кэш Net.Coffee", Find(sample, "Область оценки"));
Equal("one.test, two.test", Find(sample, "Связанные домены"));
Equal("malware: Нет\nspam: Да", Find(sample, "Обнаруженные угрозы"));
Equal("Время ожидания истекло", Find(sample, "География"));
Equal("Приблизительно по стране Net.Coffee", Find(sample, "Источник пояса"));
static void ToneIs(StatisticTone expected, IReadOnlyList<StatisticLine> rows, string label)
{
    var actual = rows.Single(r => r.Label == label).Tone;
    if (expected != actual) throw new Exception($"{label}: expected {expected}, got {actual}");
}
ToneIs(StatisticTone.Neutral, empty, "VPN (по базе)");
ToneIs(StatisticTone.Bad, sample, "Score Net.Coffee");
ToneIs(StatisticTone.Good, sample, "VPN (по базе)");
ToneIs(StatisticTone.Warning, sample, "Источник пояса");
ToneIs(StatisticTone.Neutral, sample, "Область оценки");
ToneIs(StatisticTone.Warning, sample, "География");
foreach (var (score, tone) in new[] { (0, StatisticTone.Bad), (24, StatisticTone.Bad),
    (25, StatisticTone.Warning), (49, StatisticTone.Warning), (50, StatisticTone.Good),
    (79, StatisticTone.Good), (80, StatisticTone.Good), (95, StatisticTone.Good), (100, StatisticTone.Good) })
    ToneIs(tone, Rows($$"""{"score":{{score}}} """), "Score Net.Coffee");
var risks = Rows("""{"iprisk":{"is_vpn":true,"is_proxy":false,"is_tor":null,"is_abuser":"false","isResidential":true,"is_datacenter":true,"countryCode":"RU"}}""");
ToneIs(StatisticTone.Bad, risks, "VPN (по базе)");
ToneIs(StatisticTone.Good, risks, "Прокси (по базе)");
ToneIs(StatisticTone.Neutral, risks, "Tor (по базе)");
ToneIs(StatisticTone.Neutral, risks, "Записи о злоупотреблениях (по базе)");
ToneIs(StatisticTone.Good, risks, "Жилой IP (по базе)");
ToneIs(StatisticTone.Warning, risks, "Дата-центр (по базе)");
ToneIs(StatisticTone.Bad, risks, "Код страны · Risk API");
ToneIs(StatisticTone.Warning, Rows("""{"iprisk":{"isResidential":false}}"""), "Жилой IP (по базе)");
Console.WriteLine("Statistics formatting checks passed: missing fields, zero/false, distinct geography sources, CIDR, plain-text collections, partial errors, timezone source.");
Console.WriteLine("Net.Coffee color rules passed: score boundaries, risk vs residential booleans, restricted region, unknown values stay neutral.");

var settingsFolder = Path.Combine(Path.GetTempPath(), "foxbridge-preferences-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(settingsFolder);
try
{
    var settingsPath = Path.Combine(settingsFolder, "settings.json");
    File.WriteAllText(settingsPath, """{"profiles_ini":"custom/profiles.ini"}""");
    var settings = UiPreferences.Load(settingsPath);
    Equal("custom/profiles.ini", settings.ProfilesIni);
    Equal("8765", settings.Port.ToString());
    settings.ProfilePath = "profile-one";
    settings.Port = 19001;
    settings.Servers["profile-one"] = new("first.example", 443);
    settings.Servers["profile-two"] = new("second.example", 8443);
    settings.Save(settingsPath);
    settings = UiPreferences.Load(settingsPath);
    Equal("19001", settings.Port.ToString());
    Equal("profile-one", settings.ProfilePath);
    Equal("first.example", settings.Servers["PROFILE-ONE"].Host);
    Equal("8443", settings.Servers["profile-two"].Port.ToString());
    Equal("1", settings.FindServer("profile-one", [new("second.example", 8443), new("FIRST.EXAMPLE", 443)]).ToString()!);
    Equal("-1", settings.FindServer("profile-one", [new("first.example", 8443)]).ToString()!);
    if (settings.FindServer("unknown-profile", [new("first.example", 443)]) is not null)
        throw new Exception("Another profile must not inherit a saved endpoint");
    settings.ProfilesIni = "changed/profiles.ini";
    settings.Save(settingsPath);
    settings = UiPreferences.Load(settingsPath);
    Equal("19001", settings.Port.ToString());
    Equal("first.example", settings.Servers["profile-one"].Host);
    if (File.Exists(settingsPath + ".tmp")) throw new Exception("Temporary settings file retained");
    File.WriteAllText(settingsPath, """{"port":80,"servers":null}""");
    Equal("8765", UiPreferences.Load(settingsPath).Port.ToString());
    var report = await DependencyCheck.Run(settingsFolder);
    if (report.PythonPath is not null || report.Problem is null || !report.Problem.Contains("worker.py"))
        throw new Exception("Incomplete application folder must report missing backend files");
    // This test is local only. It checks actual interpreter discovery without account access.
    var backend = Path.Combine(settingsFolder, "Backend");
    Directory.CreateDirectory(backend);
    foreach (var name in DependencyCheck.RequiredFiles) File.WriteAllText(Path.Combine(backend, name), "");
    report = await DependencyCheck.Run(settingsFolder);
    if (report.PythonPath is not null)
    {
        if (!File.Exists(report.PythonPath) || report.Problem is not null) throw new Exception("Invalid Python discovery result");
        Console.WriteLine("Python interpreter discovery passed.");
    }
    else if (report.Problem is null || !report.Problem.Contains("Python")) throw new Exception("Missing Python must include installation guidance");
}
finally { Directory.Delete(settingsFolder, recursive: true); }
Console.WriteLine("Preferences checks passed: legacy migration, port restore, per-profile endpoints, atomic updates, invalid port fallback. Dependency file checks passed.");
foreach (var (score, expected) in new (double?, uint)[]
{
    (null, 0xFF9CA3AF), (double.NaN, 0xFF9CA3AF), (-1, 0xFF9CA3AF), (101, 0xFF9CA3AF),
    (0, 0xFFEF4444), (24.99, 0xFFEF4444), (25, 0xFFEAB308), (49.99, 0xFFEAB308),
    (50, 0xFF84CC16), (79.99, 0xFF84CC16), (80, 0xFF16A34A), (94.99, 0xFF16A34A),
    (95, 0xFF22C55E), (100, 0xFF22C55E)
})
    if (StatisticsFormatter.ScoreColor(score) != expected) throw new Exception($"Incorrect score color: {score}");
Console.WriteLine("Net.Coffee score colors passed: all boundaries, zero and missing/invalid values.");
