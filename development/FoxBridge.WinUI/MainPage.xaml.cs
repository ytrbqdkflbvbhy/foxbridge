using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Security.Principal;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace FoxBridge_WinUI;

public sealed partial class MainPage : Page
{
    private readonly BridgeClient client = new();
    private readonly bool administrator = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
    private readonly DispatcherTimer displayTimer = new();
    private bool windowVisible = true, windowActive = true, updatingObservation;
    private (string? Session, int Interval) observation;
    private JsonElement? activitySnapshot;
    private long activityTimestamp;
    private JsonElement? healthSnapshot;
    private long healthTimestamp;
    private bool initialized, backendReady, busy, connected, ready, checking, fresh, closing;
    private string? session, launchTimezone;
    private string health = "stopped";
    private string connectionProgress = "";
    private DateTimeOffset refreshAt = DateTimeOffset.MinValue;
    private JsonElement? snapshot;
    private bool pendingLaunch;
    private bool browserOpening;
    private FirefoxSession? browser;
    private Task? browserLaunchTask;
    private readonly CancellationTokenSource browserLifetime = new();
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
        InitializeReadme();
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
        displayTimer.Tick += (_, _) =>
        {
            UpdateActivityDisplay();
            UpdateHealthDisplay();
            UpdateRefreshButton();
            UpdateDisplayScheduling();
        };
    }

    private async void Start(object sender, RoutedEventArgs e)
    {
        Loaded -= Start;
        PageRoot.Focus(FocusState.Programmatic);
        UpdateNavigationFooter();
#if DEBUG
        var arguments = Environment.GetCommandLineArgs();
        if (arguments.Length == 4 && arguments[1] == "--preview-layout")
        {
            // Offline layout QA: never opens a Firefox profile or starts the worker.
            SetQuota(70, "35.00", "ГиБ", "Демонстрационные данные квоты");
            Render();
            return;
        }
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
            ModeBox.SelectedIndex = preferences.Tun ? 1 : 0;
        }
        catch (Exception) { Log("Сохранённые настройки недоступны. Используем автоматический поиск."); }
        finally { restoringPreferences = false; }
        AdminWarning.IsOpen = !administrator;
        await InitializeDependencies();
        Render();
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            if (!closing && Dashboard.Visibility == Visibility.Visible) App.MainWindow?.FitStartupContent(this);
        });
    }

    public double MeasureStartupHeight()
    {
        if (PageRoot.ActualWidth <= 0) return 0;
        // Measure natural content height instead of the viewport's stretched height.
        PageRoot.Measure(new Windows.Foundation.Size(PageRoot.ActualWidth, double.PositiveInfinity));
        return PageRoot.DesiredSize.Height + Math.Max(0, ActualHeight - ContentScroll.ActualHeight);
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
    private static bool HasScore(JsonElement value) => value.TryGetProperty("score", out var score)
        && score.ValueKind == JsonValueKind.Number && score.TryGetDouble(out var number) && number is >= 0 and <= 100;

    private void SetScore(double? score)
    {
        if (score is not (>= 0 and <= 100)) score = null;
        ScoreText.Text = score?.ToString("0.#", CultureInfo.InvariantCulture) ?? "—";
        ScoreBar.Value = score ?? 0;
        var color = StatisticsFormatter.ScoreColor(score);
        var brush = new SolidColorBrush(Windows.UI.Color.FromArgb(255, (byte)(color >> 16), (byte)(color >> 8), (byte)color));
        ScoreText.Foreground = ScoreBar.Foreground = brush;
    }

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
            case "connect_progress":
                connectionProgress = Text(data, "message");
                Log(connectionProgress);
                break;
            case "connect":
                activitySnapshot = null;
                healthSnapshot = null;
                ActivityText.Text = "Ожидаем данные подключения…";
                connectionProgress = "";
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
                UpdateHealth(data.GetProperty("health"));
                refreshAt = Flag(data, "prechecked") ? DateTimeOffset.UtcNow.AddSeconds(10) : DateTimeOffset.MinValue;
                SetScore(null);
                Notice.IsOpen = false;
                Log(Flag(data, "tun") ? "TUN включён для компьютера. TCP и DNS идут через FoxBridge; остальной UDP блокируется."
                    : "Прокси включён. Запрашиваем оценку и часовой пояс.");
                if (!Flag(data, "prechecked")) await Check();
                break;
            case "disconnect":
                activitySnapshot = null;
                healthSnapshot = null;
                ActivityText.Text = "Прокси выключен";
                ChannelText.Text = "Канал не запущен";
                busy = false;
                connected = checking = fresh = false;
                SetScore(null);
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
                if (!connected || data.ValueKind != JsonValueKind.Object || Text(data, "session_id") != session) return;
                activitySnapshot = data;
                activityTimestamp = Stopwatch.GetTimestamp();
                UpdateActivityDisplay();
                return;
            case "check":
                if (!connected || Text(data, "session_id") != session) break;
                checking = false;
                if (Flag(data, "discarded")) break;
                refreshAt = Max(refreshAt, DateTimeOffset.UtcNow.AddSeconds(Number(data, "retry_after")));
                statisticsData = data;
                ShowStatistics(data);
                if (!HasScore(data))
                {
                    SetScore(null);
                    fresh = false;
                    ShowError("Не удалось получить score Net.Coffee. Попробуйте обновить оценку.");
                    ScoreNote.Text = "Оценка не обновлена. Предыдущие данные неактуальны.";
                    break;
                }
                snapshot = data;
                fresh = health == "online";
                SetScore(Number(data, "score"));
                ScoreNote.Text = $"Проверено: {DateTime.Now:HH:mm:ss} · IP {Text(data, "ip")}";
                PrepareTimezone(data);
                Log("Оценка Net.Coffee получена.");
                break;
            case "tun_lost":
                ShowError("TUN неожиданно остановился. Подключение отключается; Windows возвращается к обычному доступу в сеть.");
                busy = true;
                await Send("disconnect");
                break;
            case "quota": ShowQuota(data); break;
            case "commands":
                if (CanLaunch() && Text(data, "session_id") == session && pendingLaunch)
                    Launch(Text(data, "text"));
                pendingLaunch = false;
                break;
            case "warning": Log(data.GetString() ?? "Предупреждение сети"); break;
            case "error":
                connectionProgress = "";
                var operation = Text(data, "operation");
                if (operation == "check")
                {
                    if (Text(data, "session_id") != session) break;
                    SetScore(null);
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
            LaunchNote.Text = $"TZ терминала: {candidate} · UTC{(offset < TimeSpan.Zero ? "−" : "+")}{offset.Duration():hh\\:mm}. " + (Flag(data, "timezone_estimated")
                ? "Приблизительный пояс по стране Net.Coffee." : "Пояс из ответа Net.Coffee.");
        }
        catch (Exception error) when (error is TimeZoneNotFoundException or InvalidTimeZoneException)
        {
            LaunchNote.Text = "Обновите оценку. Для терминала нужен корректный пояс Net.Coffee.";
        }
    }

    private void UpdateHealth(JsonElement data)
    {
        if (Text(data, "session_id") != session) return;
        health = Text(data, "state", "stopped");
        if (health != "online") fresh = false;
        healthSnapshot = data;
        healthTimestamp = Stopwatch.GetTimestamp();
        UpdateHealthDisplay();
    }

    private void UpdateHealthDisplay()
    {
        if (!windowVisible || healthSnapshot is not { } data || Text(data, "session_id") != session) return;
        var retry = Math.Max(0, Number(data, "retry_in") - Stopwatch.GetElapsedTime(healthTimestamp).TotalSeconds);
        if (DashboardVisible)
        {
            RetryButton.IsEnabled = !(Text(data, "code") is "quota" or "rate_limit") || retry <= 0;
            if (health == "recovering") ScoreNote.Text = "Канал восстанавливается. Оценка неактуальна.";
        }
        if (DetailsVisible)
        {
            var text = health == "online" ? "Канал Firefox доступен." : $"{Text(data, "message")} Повтор через {Math.Ceiling(retry):0} с.";
            if (ChannelText.Text != text) ChannelText.Text = text;
        }
    }

    private void Render()
    {
        if (!initialized) return;
        UpdateNoticesVisibility();
        BusyRing.IsActive = busy || checking;
        StatusText.Text = busy ? connectionProgress.Length > 0 ? connectionProgress : "Подождите…" : connected ? health == "online" ? "Подключено" : "Восстанавливаем канал" : ready ? "Готово к подключению" : "Не подключено";
        StateDot.Fill = new SolidColorBrush(connected && health == "online" ? Windows.UI.Color.FromArgb(255, 108, 214, 167) : busy || connected ? Windows.UI.Color.FromArgb(255, 255, 170, 123) : Colors.Gray);
        var tun = ModeBox.SelectedIndex == 1;
        ConnectButton.Content = connected ? tun ? "Отключить TUN" : "Выключить прокси" : tun ? "Подключить TUN" : "Включить прокси";
        ConnectButton.IsEnabled = backendReady && !busy && (connected || ready && ServerBox.SelectedItem is not null && (!tun || administrator));
        ModeBox.IsEnabled = !busy && !connected;
        ModeNote.Text = tun ? "Для направления всего трафика через прокси."
            : "Для терминала и Firefox.";
        ProfileBox.IsEnabled = LoadButton.IsEnabled = backendReady && !busy && !connected;
        LoadButton.IsEnabled &= ProfileBox.SelectedItem is not null;
        ServerBox.IsEnabled = ready && !busy && !connected;
        PortBox.IsEnabled = !busy && !connected;
        SaveSettingsButton.IsEnabled = backendReady && !busy && !connected;
        ProfilesPathBox.IsEnabled = !busy && !connected;
        UpdateRefreshButton();
        ShellButton.IsEnabled = CanLaunch() && !pendingLaunch;
        BrowserButton.IsEnabled = CanLaunchBrowser() && !browserOpening;
        BrowserButtonText.Text = browserOpening ? "Запускаем Firefox…" : "Открыть Firefox";
        RetryButton.Visibility = connected && health == "recovering" ? Visibility.Visible : Visibility.Collapsed;
        AddressText.Text = $"127.0.0.1:{(connected ? activePort : (int)(double.IsNaN(PortBox.Value) ? 8765 : PortBox.Value))}";
        if (!connected) LaunchNote.Text = "Включите прокси и дождитесь оценки IP и подготовки часового пояса.";
        DetailsStateText.Text = statisticsPreview ? "Демонстрационные данные · проверка интерфейса без подключения"
            : statisticsData is null ? "Данных пока нет. Подключитесь и обновите оценку."
            : checking ? "Обновляем сведения. Ниже показана предыдущая проверка."
            : !connected || !fresh ? "Сохранённые данные. Для актуальной статистики обновите оценку."
            : "Данные текущего подключения · Net.Coffee";
        UpdateDisplayScheduling();
    }

    public void SetWindowActivity(bool visible, bool active)
    {
        windowVisible = visible;
        windowActive = active;
        if (!initialized || closing) return;
        UpdateDisplayScheduling();
        if (visible)
        {
            UpdateActivityDisplay();
            UpdateHealthDisplay();
            UpdateRefreshButton();
        }
    }

    private bool DashboardVisible => windowVisible && Dashboard.Visibility == Visibility.Visible;
    private bool DetailsVisible => windowVisible && DetailsPanel.Visibility == Visibility.Visible;

    private void UpdateRefreshButton()
    {
        if (!DashboardVisible) return;
        var wait = Math.Max(0, (int)Math.Ceiling((refreshAt - DateTimeOffset.UtcNow).TotalSeconds));
        RefreshStatsButton.IsEnabled = backendReady && connected && !busy && !checking && wait == 0;
        var text = checking ? "Проверяем…" : wait > 0 && connected ? $"Обновить через {wait} с" : "Обновить оценку";
        if (!Equals(RefreshStatsButton.Content, text)) RefreshStatsButton.Content = text;
    }

    private void UpdateActivityDisplay()
    {
        if (!DetailsVisible || !connected || activitySnapshot is not { } data) return;
        var uptime = Number(data, "uptime") + Stopwatch.GetElapsedTime(activityTimestamp).TotalSeconds;
        var text = $"Соединений: {Number(data, "active"):0}   ·   ↑ {Bytes(Number(data, "up"))}   ↓ {Bytes(Number(data, "down"))}   ·   {TimeSpan.FromSeconds(uptime):hh\\:mm\\:ss}";
        if (ActivityText.Text != text) ActivityText.Text = text;
    }

    private (string? Session, int Interval) DesiredObservation =>
        !closing && backendReady && connected && DetailsVisible
            ? (session, windowActive ? 2 : 10) : (null, 0);

    private void UpdateDisplayScheduling()
    {
        if (!closing && connected && (DetailsVisible ||
            DashboardVisible && (health == "recovering" || refreshAt > DateTimeOffset.UtcNow)))
        {
            // The only periodic UI work is visible elapsed time / cooldown text.
            var interval = TimeSpan.FromSeconds(windowActive ? 1 : 10);
            if (displayTimer.Interval != interval) displayTimer.Interval = interval;
            if (!displayTimer.IsEnabled) displayTimer.Start();
        }
        else displayTimer.Stop();
        if (!updatingObservation && observation != DesiredObservation)
            _ = UpdateObservationAsync();
    }

    private async Task UpdateObservationAsync()
    {
        updatingObservation = true;
        try
        {
            while (!closing && backendReady && observation != DesiredObservation)
            {
                var desired = DesiredObservation;
                await Send("observe_activity", new { session_id = desired.Session, interval = desired.Interval });
                observation = desired;
            }
        }
        finally { updatingObservation = false; }
    }

    private bool CanLaunch() => backendReady && connected && !busy && !checking && fresh && health == "online" && launchTimezone is not null && snapshot is { } s && Text(s, "session_id") == session && HasScore(s);
    private bool CanLaunchBrowser() => backendReady && connected && !busy && !checking && fresh && health == "online" && snapshot is { } s && Text(s, "session_id") == session && HasScore(s);
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
        QuotaBar.Value = percent;
        QuotaValue.Text = value;
        QuotaUnit.Text = unit;
        ToolTipService.SetToolTip(QuotaCard, description);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(QuotaBar, description);
    }
    private void Log(string text)
    {
        LogText.Text += $"{DateTime.Now:HH:mm:ss}  {text}\n";
        if (LogText.Text.Length > 24000) LogText.Text = LogText.Text[^18000..];
    }
    private void ShowError(string text) { Notice.Message = text; Notice.IsOpen = true; UpdateNoticesVisibility(); Log(text); }
    private void WarningClosed(InfoBar sender, InfoBarClosedEventArgs args) => UpdateNoticesVisibility();
    private void UpdateNoticesVisibility()
    {
        if (NoticesPanel is not null && Notice is not null && AdminWarning is not null)
            NoticesPanel.Visibility = Notice.IsOpen || AdminWarning.IsOpen ? Visibility.Visible : Visibility.Collapsed;
    }

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
        connectionProgress = "";
        if (busy || !backendReady) return;
        if (!connected && ModeBox.SelectedIndex == 1 && !administrator)
        { AdminWarning.IsOpen = true; return; }
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
            await Send("connect", new { server_index = index, port = (int)PortBox.Value, tun = ModeBox.SelectedIndex == 1 });
        }
    }
    private void ModeChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!initialized || restoringPreferences) return;
        preferences.Tun = ModeBox.SelectedIndex == 1;
        PersistPreferences();
        Render();
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
    private async void OpenBrowser(object sender, RoutedEventArgs e)
    {
        if (!CanLaunchBrowser() || browserOpening) return;
        browserLaunchTask = LaunchBrowser();
        await browserLaunchTask;
    }
    private async Task LaunchBrowser()
    {
        browserOpening = true;
        Render();
        var launchSession = session;
        var profilePath = (ProfileBox.SelectedItem as ComboBoxItem)?.Tag as string;
        try
        {
            if (string.IsNullOrWhiteSpace(profilePath)) throw new InvalidOperationException("Выберите и загрузите профиль Firefox для VPN.");
            if (browser is { IsOpen: true })
            {
                if (browser.Port != activePort || !string.Equals(browser.ProfilePath, Path.GetFullPath(profilePath), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Закройте Firefox FoxBridge перед запуском с другим профилем или портом.");
                await browser.OpenTab(browserLifetime.Token);
            }
            else
            {
                if (browser is not null) await browser.DisposeAsync();
                browser = null;
                browser = await FirefoxSession.Start(activePort, profilePath, browserLifetime.Token);
            }
            if (closing || !CanLaunchBrowser() || session != launchSession)
            {
                await browser.DisposeAsync();
                browser = null;
                if (!closing) ShowError("Подключение изменилось во время запуска Firefox. Повторите запуск после проверки IP.");
                return;
            }
            Log("Firefox открыт с выбранным VPN-профилем через прокси. Язык и часовой пояс браузера не изменяются.");
        }
        catch (Exception error)
        {
            if (!closing) ShowError(error is OperationCanceledException
                ? "Firefox не успел открыть профиль. Закройте его окно и повторите запуск."
                : error is InvalidOperationException ? error.Message : "Не удалось открыть Firefox. Закройте Firefox FoxBridge и повторите запуск.");
        }
        finally
        {
            browserOpening = false;
            if (!closing) Render();
        }
    }
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
            using var terminal = Process.Start(start) ?? throw new IOException();
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
        UpdateDisplayScheduling();
        UpdateActivityDisplay();
        UpdateHealthDisplay();
        UpdateRefreshButton();
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
        ProfileCards.RowSpacing = narrow ? 18 : 0;
        ProfileCards.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(220);
        Grid.SetColumn(QuotaCard, narrow ? 0 : 1);
        Grid.SetRow(QuotaCard, narrow ? 1 : 0);
        Grid.SetColumnSpan(ProfileCard, narrow ? 2 : 1);
        Grid.SetColumnSpan(QuotaCard, narrow ? 2 : 1);
        QuotaCard.HorizontalAlignment = HorizontalAlignment.Stretch;
    }
    private void AdaptNavigation(NavigationView sender, NavigationViewDisplayModeChangedEventArgs args)
    {
        UpdateNavigationFooter();
    }
    private void UpdateNavigationFooter()
    {
        if (NavigationFooter is not null)
            NavigationFooter.Visibility = Navigation.IsPaneOpen ? Visibility.Visible : Visibility.Collapsed;
    }
    private void NavigationPaneOpened(NavigationView sender, object args) => UpdateNavigationFooter();
    private void NavigationPaneClosing(NavigationView sender, NavigationViewPaneClosingEventArgs args)
    {
        if (NavigationFooter is not null) NavigationFooter.Visibility = Visibility.Collapsed;
    }
    private void AdaptPage(object sender, SizeChangedEventArgs args)
    {
        if (PageRoot is null || TerminalCard is null) return;
        var panoramic = args.NewSize.Width >= 1450 && args.NewSize.Height < 720;
        var compact = args.NewSize.Width >= 740 && args.NewSize.Height < 620;
        PageRoot.MaxWidth = panoramic ? 1800 : 1200;
        PageRoot.Padding = compact ? new Thickness(20, 10, 20, 16)
            : args.NewSize.Width < 700 ? new Thickness(16, 14, 16, 20) : new Thickness(28, 14, 28, 28);
        Dashboard.ColumnDefinitions[0].Width = panoramic ? new GridLength(300) : new GridLength(1, GridUnitType.Star);
        Dashboard.ColumnDefinitions[1].Width = panoramic ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
        Grid.SetColumnSpan(ProfileCards, panoramic ? 1 : 2);
        Grid.SetColumn(TopCards, panoramic ? 1 : 0);
        Grid.SetRow(TopCards, panoramic ? 1 : 2);
        Grid.SetColumnSpan(TopCards, panoramic ? 1 : 2);
        Grid.SetRow(DashboardFooter, panoramic ? 2 : 3);
        DashboardHeader.Margin = new Thickness(0, 0, 0, compact ? 12 : 18);
        ProfileCards.Margin = new Thickness(0, 0, 0, panoramic ? 0 : compact ? 12 : 18);
        TopCards.Margin = new Thickness(0, 0, 0, compact ? 12 : 18);
        ConnectionCard.Padding = ScoreCard.Padding = TerminalCard.Padding = new Thickness(compact ? 16 : 24);
        ConnectionContent.Spacing = compact ? 10 : 17;
        ScoreContent.Spacing = TerminalContent.Spacing = compact ? 10 : 16;
        RouteSideCards.Spacing = compact ? 12 : 18;
        ScoreText.FontSize = compact ? 44 : 64;
        ScoreNote.MinHeight = compact ? 0 : 48;
        ConnectButton.Height = compact ? 36 : 44;
    }
    private void AdaptProfileControls(object sender, SizeChangedEventArgs args)
    {
        if (LoadButton is null) return;
        var narrow = args.NewSize.Width < 460;
        ProfileControls.RowSpacing = narrow ? 12 : 0;
        ProfileControls.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : GridLength.Auto;
        Grid.SetColumn(LoadButton, narrow ? 0 : 1);
        Grid.SetRow(LoadButton, narrow ? 1 : 0);
        Grid.SetColumnSpan(ProfileBox, narrow ? 2 : 1);
        Grid.SetColumnSpan(LoadButton, narrow ? 2 : 1);
        LoadButton.HorizontalAlignment = narrow ? HorizontalAlignment.Stretch : HorizontalAlignment.Left;
    }
    private void AdaptCards(object sender, SizeChangedEventArgs args)
    {
        if (RouteSideCards is null) return;
        var narrow = args.NewSize.Width < 740;
        TopCards.RowSpacing = narrow ? 18 : 0;
        TopCards.ColumnDefinitions[0].Width = narrow ? new GridLength(1, GridUnitType.Star) : new GridLength(1.6, GridUnitType.Star);
        TopCards.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        Grid.SetColumn(RouteSideCards, narrow ? 0 : 1);
        Grid.SetRow(RouteSideCards, narrow ? 1 : 0);
        Grid.SetColumnSpan(ConnectionCard, narrow ? 2 : 1);
        Grid.SetColumnSpan(RouteSideCards, narrow ? 2 : 1);
    }
    public async Task Shutdown()
    {
        closing = true;
        displayTimer.Stop();
        browserLifetime.Cancel();
        if (browserLaunchTask is not null) await browserLaunchTask;
        if (browser is not null) await browser.DisposeAsync();
        await client.Stop();
    }
}
