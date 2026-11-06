using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Windowing;
using System.Runtime.InteropServices;
using Windows.Graphics;

namespace FoxBridge_WinUI;
public sealed partial class MainWindow : Window
{
    private bool closing;
    private bool startupSizeApplied;
    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr window);

    private double WindowScale => GetDpiForWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)) / 96.0;

    private void UpdateMinimumSize()
    {
        if (AppWindow.Presenter is not OverlappedPresenter presenter) return;
        var work = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var width = Math.Min(work.Width, (int)Math.Ceiling(640 * WindowScale));
        var height = Math.Min(work.Height, (int)Math.Ceiling(560 * WindowScale));
        if (presenter.PreferredMinimumWidth != width) presenter.PreferredMinimumWidth = width;
        if (presenter.PreferredMinimumHeight != height) presenter.PreferredMinimumHeight = height;
    }
    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragRegion);
        UpdateMinimumSize();
        var work = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        AppWindow.Resize(new SizeInt32(Math.Min(work.Width, (int)(1240 * WindowScale)),
                                      Math.Min(work.Height, (int)(800 * WindowScale))));
        AppWindow.Changed += (_, args) =>
        {
            if (args.DidPositionChange || args.DidSizeChange || args.DidPresenterChange) UpdateMinimumSize();
        };
#if DEBUG
        var arguments = Environment.GetCommandLineArgs();
        if (arguments.Length == 4 && arguments[1] == "--preview-layout"
            && int.TryParse(arguments[2], out var width) && int.TryParse(arguments[3], out var height))
            AppWindow.Resize(new SizeInt32((int)(Math.Max(640, width) * WindowScale),
                                          (int)(Math.Max(560, height) * WindowScale)));
#endif
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
        AppWindow.TitleBar.ButtonBackgroundColor = Colors.Transparent;
        AppWindow.TitleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
        AppWindow.TitleBar.ButtonForegroundColor = Colors.White;
        RootFrame.Navigate(typeof(MainPage));
        AppWindow.Closing += async (_, args) =>
        {
            if (closing) return;
            args.Cancel = true;
            closing = true;
            try
            {
                if (RootFrame.Content is MainPage page) await page.Shutdown();
            }
            finally { Close(); }
        };
    }
    public void FitStartupContent(MainPage page)
    {
        if (startupSizeApplied || closing) return;
        startupSizeApplied = true;
        if (AppWindow.Presenter is not OverlappedPresenter { State: OverlappedPresenterState.Restored }) return;
        WindowRoot.UpdateLayout();
        var contentHeight = page.MeasureStartupHeight();
        if (contentHeight <= 0) return;
        var work = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var frameHeight = AppWindow.Size.Height - AppWindow.ClientSize.Height;
        var titleHeight = Math.Max(0, WindowRoot.ActualHeight - RootFrame.ActualHeight);
        var minimum = Math.Min(work.Height, (int)Math.Ceiling(560 * WindowScale));
        var height = (int)Math.Ceiling((contentHeight + titleHeight + 2) * WindowScale) + frameHeight;
        height = Math.Clamp(height, minimum, work.Height);
        AppWindow.ResizeClient(new SizeInt32(AppWindow.ClientSize.Width, Math.Max(1, height - frameHeight)));
        var top = Math.Clamp(AppWindow.Position.Y, work.Y, work.Y + work.Height - height);
        if (top != AppWindow.Position.Y) AppWindow.Move(new PointInt32(AppWindow.Position.X, top));
    }

    public void SetTheme(ElementTheme theme)
    {
        WindowRoot.RequestedTheme = theme;
        AppWindow.TitleBar.ButtonForegroundColor = WindowRoot.ActualTheme == ElementTheme.Light ? Colors.Black : Colors.White;
    }
}
