using Microsoft.UI;
using Microsoft.UI.Xaml;
using Windows.Graphics;

namespace FoxBridge_WinUI;
public sealed partial class MainWindow : Window
{
    private bool closing;
    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragRegion);
        AppWindow.Resize(new SizeInt32(1240, 1000));
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
    public void SetTheme(ElementTheme theme)
    {
        WindowRoot.RequestedTheme = theme;
        AppWindow.TitleBar.ButtonForegroundColor = WindowRoot.ActualTheme == ElementTheme.Light ? Colors.Black : Colors.White;
    }
}
