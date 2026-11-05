using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace FoxBridge_WinUI;

public sealed partial class MainPage : Page
{
    private readonly BridgeClient client = new();
    private readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(2) };
    private bool initialized, backendReady, busy, connected, ready, checking, fresh, closing;
    private string? session, launchTimezone;
    private string health = "stopped";
    private DateTimeOffset refreshAt = DateTimeOffset.MinValue;
    private JsonElement? snapshot;
    private bool pendingLaunch;
    private JsonElement? statisticsData;
    private bool statisticsPreview = false;
    private readonly List<Border> statisticsDividers = [];
    private int activePort = 8765;
    private UiPreferences preferences = new();
    private bool restoringPreferences, dependencyChecking, backendStarted;
    private string? pythonPath;
    private readonly List<SavedServer> availableServers = [];
    private readonly string settingsFile = Path.Combine(AppContext.BaseDirectory, "foxbridge-ui-settings.json");

    public MainPage()
    {
        InitializeComponent();
        initialized = true;
        client.Message += (kind, data) => DispatcherQueue.TryEnqueue(() => { if (!closing) Handle(kind, data); });
        client.Failed += text => DispatcherQueue.TryEnqueue(() =>
        {
            backendReady = connected = ready = fresh = busy = checking = false;
            health = "stopped";
            ShowError(text);
            Render();
        });
        Loaded += Start;
        timer.Tick += async (_, _) =>
        {
            Render();
            if (backendReady && connected && !busy && !checking) await Send("activity");
        };
    }

    private async void Start(object sender, RoutedEventArgs e)
    {
        Loaded -= Start;
        PageRoot.Focus(FocusState.Programmatic);
#if DEBUG
        var arguments = Environment.GetCommandLineArgs();
        if (arguments.Length == 3 && arguments[1] == "--preview-statistics")
        {
            // Offline visual QA only: no account/profile access or proxy startup.
            try
            {
                using var preview = JsonDocument.Parse(File.ReadAllText(arguments[2]));
                statisticsPreview = true;
                statisticsData = preview.RootElement.Clone();
                ShowStatistics(statisticsData.Value);
                Navigation.SelectedItem = Navigation.MenuItems[1];
                Render();
            }
            catch (Exception) { ShowError("Не удалось открыть демонстрационную статистику."); }
            return;
        }
#endif
        try
        {
            preferences = UiPreferences.Load(settingsFile);
            restoringPreferences = true;
            ProfilesPathBox.Text = preferences.ProfilesIni;
            PortBox.Value = preferences.Port;
        }
        catch (Exception) { Log("Сохранённые настройки недоступны. Используем автоматический поиск."); }
        finally { restoringPreferences = false; }
        await InitializeDependencies();
        Render();
    }

    private async Task InitializeDependencies()
    {
        if (dependencyChecking) return;
        dependencyChecking = true;
        DependencyText.Text = "Проверяем компоненты…";
        DependencyCheckButton.IsEnabled = false;
        try
        {
            var report = await DependencyCheck.Run(AppContext.BaseDirectory);
            if (closing) return;
            DependencyText.Text = report.Summary + (report.Problem is null ? "" : "\n\n" + report.Problem);
            if (report.Problem is not null) { ShowError(report.Problem); return; }
            pythonPath = report.PythonPath;
            if (!backendStarted)
            {
                client.Start(pythonPath!);
                backendStarted = true;
                timer.Start();
                Log("Компоненты проверены. Сетевое ядро запущено.");
                Notice.IsOpen = false;
            }
        }
        catch (Exception) { ShowError("Не удалось проверить компоненты или запустить ядро. Проверьте папку приложения и повторите проверку в настройках."); }
        finally
        {
            dependencyChecking = false;
            DependencyCheckButton.IsEnabled = true;
            Render();
        }
    }

    private async void CheckDependencies(object sender, RoutedEventArgs e) => await InitializeDependencies();

    private static string Text(JsonElement value, string key, string fallback = "") =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.String
            ? item.GetString() ?? fallback : fallback;
    private static double Number(JsonElement value, string key) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var item) && item.TryGetDouble(out var n) ? n : 0;
    private static bool Flag(JsonElement value, string key) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.True;
    private static bool HasScore(JsonElement value) => value.TryGetProperty("score", out var score) && score.ValueKind == JsonValueKind.Number;

    private async Task Send(string operation, object? args = null)
    {
        try { await client.Send(operation, args); }
        catch (Exception) { busy = checking = false; backendReady = false; fresh = false; ShowError("Не удалось передать команду ядру. Перезапустите приложение."); Render(); }
    }

    private async void Handle(string kind, JsonElement data)
    {
        switch (kind)
        {
            case "ready":
                backendReady = true;
                await Discover();
                break;
            case "profiles":
                busy = false;
                ProfileBox.Items.Clear();
                foreach (var item in data.EnumerateArray())
                    ProfileBox.Items.Add(new ComboBoxItem { Content = Text(item, "name"), Tag = Text(item, "path") });
                var preferredProfile = ProfileBox.Items.Cast<ComboBoxItem>().FirstOrDefault(item =>
                    string.Equals(item.Tag as string, preferences.ProfilePath, StringComparison.OrdinalIgnoreCase));
                ProfileBox.SelectedItem = preferredProfile ?? ProfileBox.Items.FirstOrDefault();
                Log($"Найдено профилей Firefox: {ProfileBox.Items.Count}. Загрузите выбранный профиль.");
                break;
            case "load":
                busy = false;
                ready = true;
                restoringPreferences = true;
                ServerBox.Items.Clear();
                availableServers.Clear();
                var selected = 0;
                foreach (var server in data.GetProperty("servers").EnumerateArray())
                {
                    var index = ServerBox.Items.Count;
                    var label = $"{Text(server, "country")} · {Text(server, "city")}";
                    ServerBox.Items.Add(new ComboBoxItem { Content = label, Tag = index });
                    availableServers.Add(new SavedServer(Text(server, "host"), (int)Number(server, "port")));
                    if (Text(server, "country") == Text(data, "selected_country")) selected = index;
                }
                var profilePath = (ProfileBox.SelectedItem as ComboBoxItem)?.Tag as string;
                if (profilePath is not null && preferences.FindServer(profilePath, availableServers) is { } savedIndex)
                {
                    selected = savedIndex;
                    if (selected < 0) Log("Сохранённый сервер отсутствует в списке. Выберите сервер вручную.");
                }
                ServerBox.SelectedIndex = selected;
                restoringPreferences = false;
                ShowQuota(data.GetProperty("quota"));
                Log($"Профиль загружен. Серверов: {ServerBox.Items.Count}.");
                break;
            case "connect":
                busy = false;
                connected = true;
                fresh = false;
                launchTimezone = null;
                snapshot = null;
                statisticsData = null;
                StatisticRows.Children.Clear();
                StatisticRows.RowDefinitions.Clear();
                LaunchNote.Text = "Ожидаем оценку IP и подготовку часового пояса.";
                session = Text(data, "session_id");
                activePort = (int)Number(data, "port");
                health = Text(data.GetProperty("health"), "state", "online");
                refreshAt = DateTimeOffset.MinValue;
                ScoreText.Text = "—";
                ScoreBar.Value = 0;
                Notice.IsOpen = false;
                Log("Прокси включён. Запрашиваем оценку и часовой пояс.");
                await Check();
                break;
            case "disconnect":
                busy = false;
                connected = checking = fresh = false;
                session = launchTimezone = null;
                health = "stopped";
                ScoreNote.Text = "Прокси выключен. Предыдущая оценка неактуальна.";
                Log("Прокси выключен. Активные туннели закрыты.");
                break;
            case "health":
                if (Text(data, "session_id") != session || !connected) break;
                var previous = health;
                UpdateHealth(data);
                if (previous == "recovering" && health == "online")
                {
                    Log("Канал восстановлен. Обновляем статистику.");
                    if (DateTimeOffset.UtcNow >= refreshAt) await Check();
                }
                break;
            case "activity":
                if (!connected || data.ValueKind != JsonValueKind.Object) break;
                UpdateHealth(data.GetProperty("health"));
                ActivityText.Text = $"Соединений: {Number(data, "active"):0}   ·   ↑ {Bytes(Number(data, "up"))}   ↓ {Bytes(Number(data, "down"))}   ·   {TimeSpan.FromSeconds(Number(data, "uptime")):hh\\:mm\\:ss}";
                break;
            case "check":
                if (!connected || Text(data, "session_id") != session) break;
                checking = false;
                if (Flag(data, "discarded")) break;
                refreshAt = Max(refreshAt, DateTimeOffset.UtcNow.AddSeconds(Number(data, "retry_after")));
                statisticsData = data;
                ShowStatistics(data);
                if (!HasScore(data))
                {
                    fresh = false;
                    ShowError("Не удалось получить score Net.Coffee. Попробуйте обновить оценку.");
                    ScoreNote.Text = "Оценка не обновлена. Предыдущие данные неактуальны.";
                    break;
                }
                snapshot = data;
                fresh = health == "online";
                ScoreText.Text = Number(data, "score").ToString("0.#", CultureInfo.InvariantCulture);
                ScoreBar.Value = Number(data, "score");
                ScoreNote.Text = $"Проверено: {DateTime.Now:HH:mm:ss} · IP {Text(data, "ip")}";
                PrepareTimezone(data);
                Log("Оценка Net.Coffee получена.");
                break;
            case "quota": ShowQuota(data); break;
            case "commands":
                if (CanLaunch() && Text(data, "session_id") == session && pendingLaunch)
                    Launch(Text(data, "text"));
                pendingLaunch = false;
                break;
            case "warning": Log(data.GetString() ?? "Предупреждение сети"); break;
            case "error":
                var operation = Text(data, "operation");
                if (operation == "check")
                {
                    if (Text(data, "session_id") != session) break;
                    checking = false;
                    fresh = false;
                    refreshAt = Max(refreshAt, DateTimeOffset.UtcNow.AddSeconds(Number(data, "retry_after")));
                }
                else if (operation == "load") { ready = false; busy = false; }
                else if (operation is "profiles" or "connect" or "disconnect") busy = false;
                pendingLaunch = false;
                ShowError(Text(data, "message", "Операция не выполнена."));
                break;
        }
        Render();
    }

    private void ShowStatistics(JsonElement data)
    {
        StatisticRows.Children.Clear();
        foreach (var item in StatisticsFormatter.Rows(data))
        {
            if (item.Heading)
            {
                StatisticRows.Children.Add(new TextBlock
                {
                    Text = item.Label, TextWrapping = TextWrapping.Wrap,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 16,
                    Margin = new Thickness(0, 24, 0, 8)
                });
                continue;
            }
            var valueStyle = (Style)Resources["Statistic" + item.Tone];
            var pair = new Grid { ColumnSpacing = 12 };
            pair.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            pair.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.25, GridUnitType.Star) });
            pair.Children.Add(new TextBlock
            {
                Text = item.Label, Style = (Style)Resources["Muted"],
                TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true
            });
            var result = new Grid { ColumnSpacing = 6 };
            result.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            result.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var status = item.Tone switch
            {
                StatisticTone.Good => "Норма", StatisticTone.Warning => "Предупреждение",
                StatisticTone.Bad => "Риск", _ => "Без оценки"
            };
            var marker = new TextBlock
            {
                Text = item.Tone == StatisticTone.Neutral ? "" : "●", Style = valueStyle,
                FontSize = 8, Margin = new Thickness(0, 5, 0, 0)
            };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(marker, status);
            result.Children.Add(marker);
            var value = new TextBlock
            {
                Text = item.Value, Style = valueStyle, TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true
            };
            Grid.SetColumn(value, 1);
            result.Children.Add(value);
            Grid.SetColumn(result, 1);
            pair.Children.Add(result);
            var cell = new Border { Child = pair, Style = (Style)Resources["StatisticCell"] };
            ToolTipService.SetToolTip(cell, $"{item.Label}: {item.Value} · {status}");
            StatisticRows.Children.Add(cell);
        }
        ArrangeStatistics();
    }

    private void AdaptStatistics(object sender, SizeChangedEventArgs args)
    {
        if (args.PreviousSize.Width != args.NewSize.Width) ArrangeStatistics();
    }

    private void ArrangeStatistics()
    {
        if (StatisticRows is null) return;
        var columns = StatisticRows.ActualWidth >= 700 ? 2 : 1;
        foreach (var divider in statisticsDividers)
            if (StatisticRows.Children.Contains(divider)) StatisticRows.Children.Remove(divider);
        statisticsDividers.Clear();
        StatisticRows.RowDefinitions.Clear();
        int row = 0, column = 0;
        int sectionStart = -1;
        void FinishSection()
        {
            var end = row + (column > 0 ? 1 : 0);
            if (columns != 2 || sectionStart < 0 || end <= sectionStart) return;
            var divider = new Border { Style = (Style)Resources["StatisticDivider"] };
            Grid.SetRow(divider, sectionStart);
            Grid.SetRowSpan(divider, end - sectionStart);
            statisticsDividers.Add(divider);
        }
        foreach (var element in StatisticRows.Children.Cast<FrameworkElement>())
        {
            var heading = element is TextBlock;
            if (heading) FinishSection();
            if (heading && column > 0) { row++; column = 0; }
            while (StatisticRows.RowDefinitions.Count <= row)
                StatisticRows.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow(element, row);
            Grid.SetColumn(element, column);
            Grid.SetColumnSpan(element, heading || columns == 1 ? 2 : 1);
            if (heading || ++column == columns) { row++; column = 0; }
            if (heading) sectionStart = row;
        }
        FinishSection();
        foreach (var divider in statisticsDividers) StatisticRows.Children.Add(divider);
    }

    private void PrepareTimezone(JsonElement data)
    {
        launchTimezone = null;
        var candidate = Text(data, "timezone_candidate");
        try
        {
            if (candidate.Length > 100 || !Regex.IsMatch(candidate, @"\A[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*\z"))
                throw new TimeZoneNotFoundException();
            var zone = TimeZoneInfo.FindSystemTimeZoneById(candidate);
            var offset = zone.GetUtcOffset(DateTimeOffset.UtcNow);
            launchTimezone = candidate;
            LaunchNote.Text = $"TZ: {candidate} · UTC{(offset < TimeSpan.Zero ? "−" : "+")}{offset.Duration():hh\\:mm}. " + (Flag(data, "timezone_estimated")
                ? "Приблизительный пояс по стране Net.Coffee. TZ будет передан новому терминалу и его дочерним процессам."
                : "Пояс из ответа Net.Coffee. TZ будет передан новому терминалу и его дочерним процессам.");
        }
        catch (Exception error) when (error is TimeZoneNotFoundException or InvalidTimeZoneException)
        {
            LaunchNote.Text = "Обновите оценку. Запуск терминала ждёт корректного пояса Net.Coffee.";
        }
    }

    private void UpdateHealth(JsonElement data)
    {
        if (Text(data, "session_id") != session) return;
        health = Text(data, "state", "stopped");
        if (health != "online") fresh = false;
        var retry = Number(data, "retry_in");
        RetryButton.IsEnabled = !(Text(data, "code") is "quota" or "rate_limit") || retry <= 0;
        ChannelText.Text = health == "online" ? "Канал Firefox доступен." : $"{Text(data, "message")} Повтор через {retry:0} с.";
        if (health == "recovering") ScoreNote.Text = "Канал восстанавливается. Оценка неактуальна.";
    }

    private void Render()
    {
        if (!initialized) return;
        BusyRing.IsActive = busy || checking;
        StatusText.Text = busy ? "Подождите…" : connected ? health == "online" ? "Подключено" : "Восстанавливаем канал" : ready ? "Готово к подключению" : "Не подключено";
        StateDot.Fill = new SolidColorBrush(connected && health == "online" ? Windows.UI.Color.FromArgb(255, 108, 214, 167) : busy || connected ? Windows.UI.Color.FromArgb(255, 255, 170, 123) : Colors.Gray);
        ConnectButton.Content = connected ? "Выключить прокси" : "Включить прокси";
        ConnectButton.IsEnabled = backendReady && !busy && (connected || ready && ServerBox.SelectedItem is not null);
        ProfileBox.IsEnabled = LoadButton.IsEnabled = backendReady && !busy && !connected;
        LoadButton.IsEnabled &= ProfileBox.SelectedItem is not null;
        ServerBox.IsEnabled = ready && !busy && !connected;
        PortBox.IsEnabled = !busy && !connected;
        SaveSettingsButton.IsEnabled = backendReady && !busy && !connected;
        ProfilesPathBox.IsEnabled = !busy && !connected;
        var wait = Math.Max(0, (int)Math.Ceiling((refreshAt - DateTimeOffset.UtcNow).TotalSeconds));
        RefreshStatsButton.IsEnabled = backendReady && connected && !busy && !checking && wait == 0;
        RefreshStatsButton.Content = checking ? "Проверяем…" : wait > 0 && connected ? $"Обновить через {wait} с" : "Обновить оценку";
        ShellButton.IsEnabled = CanLaunch() && !pendingLaunch;
        RetryButton.Visibility = connected && health == "recovering" ? Visibility.Visible : Visibility.Collapsed;
        AddressText.Text = $"127.0.0.1:{(connected ? activePort : (int)(double.IsNaN(PortBox.Value) ? 8765 : PortBox.Value))}";
        if (!connected) LaunchNote.Text = "Включите прокси и дождитесь оценки IP и подготовки часового пояса.";
        DetailsStateText.Text = statisticsPreview ? "Демонстрационные данные · проверка интерфейса без подключения"
            : statisticsData is null ? "Данных пока нет. Подключитесь и обновите оценку."
            : checking ? "Обновляем сведения. Ниже показана предыдущая проверка."
            : !connected || !fresh ? "Сохранённые данные. Для актуальной статистики обновите оценку."
            : "Данные текущего подключения · Net.Coffee";
    }

    private bool CanLaunch() => backendReady && connected && !busy && !checking && fresh && health == "online" && launchTimezone is not null && snapshot is { } s && Text(s, "session_id") == session && HasScore(s);
    private static DateTimeOffset Max(DateTimeOffset a, DateTimeOffset b) => a > b ? a : b;
    private static string Bytes(double n) => n >= 1073741824 ? $"{n / 1073741824:0.00} ГиБ" : $"{n / 1048576:0.00} МиБ";
    private void ShowQuota(JsonElement quota)
    {
        if (quota.ValueKind != JsonValueKind.Object)
        {
            SetQuota(0, "…", "Нет данных", "Mozilla не вернула сведения о квоте");
            return;
        }
        if (Flag(quota, "unlimited"))
        {
            SetQuota(100, "∞", "Безлимит", "Безлимитный доступ Mozilla");
            return;
        }
        var limit = Number(quota, "max");
        var remaining = Number(quota, "remaining");
        if (limit <= 0)
        {
            SetQuota(0, "…", "Нет данных", "Mozilla не вернула корректный лимит квоты");
            return;
        }
        remaining = Math.Clamp(remaining, 0, limit);
        var gib = remaining >= 1073741824;
        SetQuota(100 * remaining / limit, $"{remaining / (gib ? 1073741824 : 1048576):0.00}", gib ? "ГиБ" : "МиБ",
            $"Осталось {Bytes(remaining)} из {Bytes(limit)} · данные Mozilla");
    }
    private void SetQuota(double percent, string value, string unit, string description)
    {
        QuotaRing.Value = percent;
        QuotaValue.Text = value;
        QuotaUnit.Text = unit;
        ToolTipService.SetToolTip(QuotaCard, description);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(QuotaRing, description);
    }
    private void Log(string text)
    {
        LogText.Text += $"{DateTime.Now:HH:mm:ss}  {text}\n";
        if (LogText.Text.Length > 24000) LogText.Text = LogText.Text[^18000..];
    }
    private void ShowError(string text) { Notice.Message = text; Notice.IsOpen = true; Log(text); }

    private async Task Discover()
    {
        ready = false; busy = true; ServerBox.Items.Clear(); Render();
        await Send("profiles", new { profiles_ini = ProfilesPathBox.Text.Trim() });
    }
    private async void LoadProfile(object sender, RoutedEventArgs e)
    {
        if (!backendReady || busy || connected || ProfileBox.SelectedItem is not ComboBoxItem item) return;
        ready = false; busy = true; ServerBox.Items.Clear(); Notice.IsOpen = false; Render();
        await Send("load", new { profile = (string)item.Tag });
    }
    private void ProfileChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!initialized) return;
        ready = false; ServerBox.Items.Clear();
        availableServers.Clear();
        SetQuota(0, "…", "Нет данных", "Загрузите выбранный профиль для получения серверов и квоты");
        if (!restoringPreferences && ProfileBox.SelectedItem is ComboBoxItem item)
        {
            preferences.ProfilePath = (string)item.Tag;
            PersistPreferences();
        }
        Render();
    }
    private void ServerChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!initialized || restoringPreferences || connected || ProfileBox.SelectedItem is not ComboBoxItem profile
            || ServerBox.SelectedItem is not ComboBoxItem server || server.Tag is not int index
            || index < 0 || index >= availableServers.Count) return;
        preferences.Servers[(string)profile.Tag] = availableServers[index];
        PersistPreferences();
        Render();
    }
    private void PortChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized || restoringPreferences || connected || !double.IsFinite(args.NewValue)
            || args.NewValue != Math.Truncate(args.NewValue) || args.NewValue is < 1024 or > 65535) return;
        preferences.Port = (int)args.NewValue;
        PersistPreferences();
        Render();
    }
    private bool PersistPreferences()
    {
        try { preferences.Save(settingsFile); return true; }
        catch (Exception) { ShowError("Не удалось сохранить выбор. Проверьте право записи в папку приложения."); return false; }
    }
    private async void ToggleConnection(object sender, RoutedEventArgs e)
    {
        if (busy || !backendReady) return;
        if (!connected && (!ready || ServerBox.SelectedItem is not ComboBoxItem)) return;
        if (!connected && (!double.IsFinite(PortBox.Value) || PortBox.Value != Math.Truncate(PortBox.Value) || PortBox.Value < 1024 || PortBox.Value > 65535))
        { ShowError("Укажите целый порт от 1024 до 65535."); return; }
        busy = true; Render();
        if (connected) await Send("disconnect");
        else
        {
            var index = (int)((ComboBoxItem)ServerBox.SelectedItem).Tag;
            preferences.Port = (int)PortBox.Value;
            if (ProfileBox.SelectedItem is ComboBoxItem profile && index < availableServers.Count)
                preferences.Servers[(string)profile.Tag] = availableServers[index];
            PersistPreferences();
            await Send("connect", new { server_index = index, port = (int)PortBox.Value });
        }
    }
    private async Task Check()
    {
        if (!connected || busy || checking || DateTimeOffset.UtcNow < refreshAt) return;
        checking = true; fresh = false; refreshAt = DateTimeOffset.UtcNow.AddSeconds(10);
        ScoreNote.Text = "Получаем оценку через выбранный прокси…"; Render();
        await Send("check", new { session_id = session });
    }
    private async void RefreshStatistics(object sender, RoutedEventArgs e) => await Check();
    private async void RetryConnection(object sender, RoutedEventArgs e) => await Send("retry", new { session_id = session });
    private async void OpenShell(object sender, RoutedEventArgs e) => await RequestCommands();
    private async Task RequestCommands()
    {
        if (!CanLaunch() || pendingLaunch) return;
        pendingLaunch = true; Render(); await Send("commands");
    }
    private void Launch(string bootstrap)
    {
        try
        {
            static string Quote(string value) => "'" + value.Replace("'", "''") + "'";
            var commands = $"$env:TZ = {Quote(launchTimezone!)}\n" + bootstrap;
            var start = new ProcessStartInfo("powershell.exe") { UseShellExecute = false };
            foreach (var argument in new[] { "-NoLogo", "-NoExit", "-EncodedCommand", Convert.ToBase64String(Encoding.Unicode.GetBytes(commands)) })
                start.ArgumentList.Add(argument);
            foreach (var key in start.Environment.Keys.Where(k => new[] { "HTTP_PROXY", "HTTPS_PROXY", "WS_PROXY", "WSS_PROXY", "ALL_PROXY", "NO_PROXY", "TZ" }.Contains(k.ToUpperInvariant())).ToArray())
                start.Environment.Remove(key);
            foreach (var key in new[] { "HTTP_PROXY", "HTTPS_PROXY", "WS_PROXY", "WSS_PROXY" }) start.Environment[key] = $"http://127.0.0.1:{activePort}";
            start.Environment["NO_PROXY"] = "localhost,127.0.0.1,::1";
            start.Environment["TZ"] = launchTimezone!;
            Process.Start(start);
            Log($"Открыт PowerShell с прокси и TZ={launchTimezone}.");
        }
        catch (Exception) { ShowError("Не удалось открыть защищённый терминал."); }
    }
    private async void SaveSettings(object sender, RoutedEventArgs e)
    {
        if (busy || connected) return;
        var path = ProfilesPathBox.Text.Trim().Trim('"');
        if (path.Length > 0)
        {
            path = Environment.ExpandEnvironmentVariables(path);
            if (!File.Exists(path) || !Path.GetFileName(path).Equals("profiles.ini", StringComparison.OrdinalIgnoreCase))
            { ShowError("Укажите существующий файл profiles.ini."); return; }
        }
        preferences.ProfilesIni = path;
        if (!PersistPreferences()) return;
        ProfilesPathBox.Text = path; await Discover();
        Navigation.SelectedItem = Navigation.MenuItems[0];
    }
    private void ChangeTheme(object sender, SelectionChangedEventArgs e)
    {
        if (!initialized) return;
        App.MainWindow?.SetTheme(ThemeBox.SelectedIndex switch { 1 => ElementTheme.Light, 2 => ElementTheme.Default, _ => ElementTheme.Dark });
    }
    private void Navigate(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (!initialized) return;
        var tag = args.IsSettingsSelected ? "settings" : (args.SelectedItem as NavigationViewItem)?.Tag as string;
        Dashboard.Visibility = tag == "dashboard" ? Visibility.Visible : Visibility.Collapsed;
        DetailsPanel.Visibility = tag == "details" ? Visibility.Visible : Visibility.Collapsed;
        LogPanel.Visibility = tag == "log" ? Visibility.Visible : Visibility.Collapsed;
        ReadmePanel.Visibility = tag == "readme" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPanel.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
    }
    private void BackgroundPointerPressed(object sender, PointerRoutedEventArgs args)
    {
        // Leave text fields, selectors, buttons and scrollbars to handle their own
        // pointer input. A blank-area click must not focus the first editable child.
        for (var element = args.OriginalSource as DependencyObject;
             element is not null && element != PageRoot;
             element = VisualTreeHelper.GetParent(element))
        {
            if (element is Control control && control.IsEnabled && control.IsTabStop
                && control is not ScrollViewer && control is not NavigationView)
                return;
        }
        PageRoot.Focus(FocusState.Pointer);
        args.Handled = true;
    }

    private void AdaptProfileCards(object sender, SizeChangedEventArgs args)
    {
        if (QuotaCard is null) return;
        var narrow = args.NewSize.Width < 650;
        ProfileCards.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(176);
        Grid.SetColumn(QuotaCard, narrow ? 0 : 1);
        Grid.SetRow(QuotaCard, narrow ? 1 : 0);
        QuotaCard.HorizontalAlignment = narrow ? HorizontalAlignment.Left : HorizontalAlignment.Stretch;
    }
    private void AdaptCards(object sender, SizeChangedEventArgs args)
    {
        if (ScoreCard is null) return;
        var narrow = args.NewSize.Width < 740;
        Grid.SetColumn(ScoreCard, narrow ? 0 : 1);
        Grid.SetRow(ScoreCard, narrow ? 1 : 0);
        Grid.SetColumnSpan(ConnectionCard, narrow ? 2 : 1);
        Grid.SetColumnSpan(ScoreCard, narrow ? 2 : 1);
    }
    public async Task Shutdown()
    {
        closing = true;
        timer.Stop();
        await client.Stop();
    }
}
