using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media.Animation;
using Windows.UI.ViewManagement;

namespace FoxBridge_WinUI;

/// <summary>A quota bar with a short, interruptible ease-out animation.</summary>
public sealed class QuotaIndicator : RangeBase
{
    private Microsoft.UI.Xaml.Controls.Border? indicator;
    private Storyboard? animation;
    private readonly UISettings uiSettings = new();

    public static readonly DependencyProperty FillProgressProperty = DependencyProperty.Register(
        nameof(FillProgress), typeof(double), typeof(QuotaIndicator),
        new PropertyMetadata(0d, (owner, _) => ((QuotaIndicator)owner).Draw()));

    public double FillProgress
    {
        get => (double)GetValue(FillProgressProperty);
        set => SetValue(FillProgressProperty, value);
    }

    public QuotaIndicator()
    {
        IsTabStop = false;
        ValueChanged += (_, _) => AnimateValue();
        SizeChanged += (_, _) => Draw();
        Unloaded += (_, _) => SnapToValue();
    }

    protected override void OnApplyTemplate()
    {
        base.OnApplyTemplate();
        indicator = GetTemplateChild("Indicator") as Microsoft.UI.Xaml.Controls.Border;
        FillProgress = TargetProgress;
        Draw();
    }

    private double TargetProgress => Maximum > Minimum
        ? Math.Clamp((Value - Minimum) / (Maximum - Minimum), 0, 1) : 0;

    private void SnapToValue()
    {
        animation?.Stop();
        animation = null;
        FillProgress = TargetProgress;
    }

    private void AnimateValue()
    {
        var from = FillProgress;
        var target = TargetProgress;
        if (!IsLoaded || target == 0 || !uiSettings.AnimationsEnabled || Math.Abs(from - target) < 0.0001)
        {
            SnapToValue();
            return;
        }
        animation?.Stop();
        FillProgress = from;
        var transition = new DoubleAnimation
        {
            From = from,
            To = target,
            Duration = new Duration(TimeSpan.FromMilliseconds(350)),
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            EnableDependentAnimation = true
        };
        var storyboard = new Storyboard();
        Storyboard.SetTarget(transition, this);
        Storyboard.SetTargetProperty(transition, nameof(FillProgress));
        storyboard.Children.Add(transition);
        storyboard.Completed += (_, _) =>
        {
            if (animation != storyboard) return;
            SnapToValue();
        };
        animation = storyboard;
        storyboard.Begin();
    }

    private void Draw()
    {
        if (indicator is null) return;
        var progress = Math.Clamp(FillProgress, 0, 1);
        indicator.Width = ActualWidth * progress;
        indicator.Visibility = progress > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    protected override AutomationPeer OnCreateAutomationPeer() => new QuotaPeer(this);

    private sealed class QuotaPeer(QuotaIndicator owner) : RangeBaseAutomationPeer(owner), IRangeValueProvider
    {
        protected override string GetClassNameCore() => nameof(QuotaIndicator);
        protected override AutomationControlType GetAutomationControlTypeCore() => AutomationControlType.ProgressBar;
        protected override object GetPatternCore(PatternInterface patternInterface) =>
            patternInterface == PatternInterface.RangeValue ? this : base.GetPatternCore(patternInterface);
        bool IRangeValueProvider.IsReadOnly => true;
        double IRangeValueProvider.Value => owner.Value;
        double IRangeValueProvider.Minimum => owner.Minimum;
        double IRangeValueProvider.Maximum => owner.Maximum;
        double IRangeValueProvider.SmallChange => 0;
        double IRangeValueProvider.LargeChange => 0;
        void IRangeValueProvider.SetValue(double value) => throw new InvalidOperationException("Quota is read-only.");
    }
}
