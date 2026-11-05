using System.Globalization;
using System.Text.Json;

namespace FoxBridge_WinUI;

internal enum StatisticTone { Neutral, Good, Warning, Bad }
internal record StatisticLine(string Label, string Value = "", bool Heading = false,
    StatisticTone Tone = StatisticTone.Neutral);

/// <summary>Provider values remain plain text; missing values are distinct from false/zero.</summary>
internal static class StatisticsFormatter
{
    // Net.Coffee claude-page.js: score gauge, boolTag and residential property tag.
    private static readonly HashSet<string> RestrictedCountries =
        ["CN", "HK", "MO", "RU", "KP", "IR", "SY", "CU", "BY", "VE"];

    internal static StatisticTone Tone(JsonElement data, string path)
    {
        var value = Field(data, path);
        if (value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return StatisticTone.Neutral;
        if (path is "score" or "raw_score")
            return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var score) && score is >= 0 and <= 100
                ? score >= 50 ? StatisticTone.Good : score >= 25 ? StatisticTone.Warning : StatisticTone.Bad
                : StatisticTone.Neutral;
        if (path is "iprisk.is_vpn" or "iprisk.is_proxy" or "iprisk.is_tor" or "iprisk.is_crawler" or "iprisk.is_abuser")
            return value.ValueKind switch
            {
                JsonValueKind.True => StatisticTone.Bad, JsonValueKind.False => StatisticTone.Good,
                _ => StatisticTone.Neutral
            };
        if (path == "iprisk.isResidential")
            return value.ValueKind switch
            {
                JsonValueKind.True => StatisticTone.Good, JsonValueKind.False => StatisticTone.Warning,
                _ => StatisticTone.Neutral
            };
        if (path == "iprisk.is_datacenter")
            return value.ValueKind == JsonValueKind.True ? StatisticTone.Warning : StatisticTone.Neutral;
        if (path == "iprisk.countryCode" && value.ValueKind == JsonValueKind.String)
        {
            var country = value.GetString()!.ToUpperInvariant();
            return country.Length != 2 ? StatisticTone.Neutral
                : RestrictedCountries.Contains(country) ? StatisticTone.Bad : StatisticTone.Good;
        }
        // Approximate TZ is FoxBridge's own warning, not a failed timezone check.
        if (path == "timezone_estimated" && value.ValueKind == JsonValueKind.True)
            return StatisticTone.Warning;
        return StatisticTone.Neutral;
    }
    public static string Value(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Undefined or JsonValueKind.Null => "Нет данных",
        JsonValueKind.True => "Да",
        JsonValueKind.False => "Нет",
        JsonValueKind.String => string.IsNullOrWhiteSpace(value.GetString()) ? "Нет данных" : value.GetString()!,
        JsonValueKind.Number => value.ToString(),
        JsonValueKind.Array => value.GetArrayLength() == 0 ? "Нет данных" : string.Join(", ", value.EnumerateArray().Select(Value)),
        JsonValueKind.Object => string.Join("\n", value.EnumerateObject().Select(p => $"{p.Name}: {Value(p.Value)}")),
        _ => "Нет данных"
    };

    private static JsonElement Field(JsonElement data, string path)
    {
        foreach (var key in path.Split('.'))
        {
            if (data.ValueKind != JsonValueKind.Object || !data.TryGetProperty(key, out var item)) return default;
            data = item;
        }
        return data;
    }

    public static IReadOnlyList<StatisticLine> Rows(JsonElement data)
    {
        var result = new List<StatisticLine>();
        void Group(string title) => result.Add(new(title, Heading: true));
        void Add(string label, string path) => result.Add(new(label, Value(Field(data, path)), Tone: Tone(data, path)));
        Group("IP и сеть");
        Add("Выходной IP для Claude", "ip");
        Add("Версия IP", "ip_version");
        Add("Страна по Claude trace", "trace.loc");
        Add("Узел Cloudflare", "trace.colo");
        Add("Сервер Firefox", "server.host");
        Add("Страна выбранного сервера", "server.country");
        Add("ASN", "iprisk.asn");
        Add("Оператор сети", "iprisk.asOrganization");
        Add("Название ASN", "iprisk.asname");
        Add("Тип сети", "iprisk.asn_kind");
        Add("Провайдер / ISP", "geoip.isp");
        Add("Компания", "iprisk.company_name");
        Add("Тип компании", "iprisk.company_type");
        Add("Подсеть CIDR", "iprisk.cidr");
        Add("Обратная DNS-запись", "iprisk.rdns");
        Add("Статус RPKI", "iprisk.rpki_status");
        Group("Оценка и риски");
        Add("Score Net.Coffee", "score");
        Add("Исходный trust score", "raw_score");
        var scope = Field(data, "score_scope");
        result.Add(new("Область оценки", (scope.ValueKind == JsonValueKind.String ? scope.GetString() : null) switch
        {
            "cidr" => "Подсеть CIDR · кэш Net.Coffee", "ip" => "Совпадающий IP", _ => "Нет данных"
        }));
        Add("IP-представитель в базе", "iprisk.ip");
        Add("Региональная корректировка", "region_adjusted");
        foreach (var (label, key) in new[]
        {
            ("Дата-центр", "is_datacenter"), ("Жилой IP", "isResidential"),
            ("Мобильная сеть", "is_mobile"), ("VPN", "is_vpn"), ("Прокси", "is_proxy"),
            ("Tor", "is_tor"), ("Краулер", "is_crawler"), ("Записи о злоупотреблениях", "is_abuser"),
            ("Bogon", "is_bogon"), ("Блокировка Reddit", "reddit_blocked")
        }) Add(label + " (по базе)", "iprisk." + key);
        Add("Оценка злоупотреблений", "iprisk.abuser_score");
        Add("Обнаруженные угрозы", "iprisk.intelligence.threats");
        Add("Дата-центр / хостинг", "iprisk.datacenter_name");
        Add("Классификация сервиса", "iprisk.ai_verdict.label");
        Add("Уверенность классификации, %", "iprisk.ai_verdict.confidence");
        Add("Пояснение сервиса", "iprisk.ai_verdict.reasoning");
        Add("Связанные домены", "iprisk.related_domains");
        Group("География и часовой пояс");
        Add("Страна · GeoIP", "geoip.country");
        Add("Регион · GeoIP", "geoip.region");
        Add("Город · GeoIP", "geoip.city");
        Add("Страна · Risk API", "iprisk.country");
        Add("Код страны · Risk API", "iprisk.countryCode");
        Add("Регион · Risk API", "iprisk.region");
        Add("Город · Risk API", "iprisk.city");
        Add("Страна регистрации сети", "iprisk.registered_country");
        Add("Часовой пояс для терминала", "timezone_candidate");
        result.Add(new("Источник пояса", Field(data, "timezone_estimated").ValueKind switch
        {
            JsonValueKind.True => "Приблизительно по стране Net.Coffee",
            JsonValueKind.False => "Поле timezone Net.Coffee", _ => "Нет данных"
        }, Tone: Tone(data, "timezone_estimated")));
        Group("Время проверки");
        var when = Field(data, "checked_at");
        result.Add(new("Проверено", when.ValueKind == JsonValueKind.String &&
            DateTimeOffset.TryParse(when.GetString(), CultureInfo.InvariantCulture, DateTimeStyles.None, out var date)
            ? date.ToLocalTime().ToString("dd.MM.yyyy HH:mm:ss zzz") : "Нет данных"));
        Add("CONNECT до Claude, мс", "connect_ms");
        Add("Запрос Net.Coffee, мс", "request_ms");
        var errors = Field(data, "errors");
        if (errors.ValueKind == JsonValueKind.Object && errors.EnumerateObject().Any())
        {
            Group("Недоступные сведения");
            foreach (var item in errors.EnumerateObject())
                result.Add(new(item.Name switch
                {
                    "geoip" => "География", "iprisk" => "Оценка риска", "connect_latency" => "Задержка подключения",
                    _ => "Источник: " + item.Name
                }, Value(item.Value), Tone: StatisticTone.Warning));
        }
        return result;
    }
}
