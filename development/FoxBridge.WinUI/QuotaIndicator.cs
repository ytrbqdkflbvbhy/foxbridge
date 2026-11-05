using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Windows.Foundation;
using Windows.UI.ViewManagement;
using ShapePath = Microsoft.UI.Xaml.Shapes.Path;

namespace FoxBridge_WinUI;

/// <summary>A determinate quota ring with a short, interruptible ease-out animation.</summary>
public sealed class QuotaIndicator : RangeBase
{
    private ShapePath? indicator;
    private Storyboard? animation;
    private readonly UISettings uiSettings = new();

    public static readonly DependencyProperty SweepAngleProperty = DependencyProperty.Register(
        nameof(SweepAngle), typeof(double), typeof(QuotaIndicator),
        new PropertyMetadata(0d, (owner, _) => ((QuotaIndicator)owner).Draw()));

    public double SweepAngle
    {
        get => (double)GetValue(SweepAngleProperty);
        set => SetValue(SweepAngleProperty, value);
    }

    public QuotaIndicator()
    {
        IsTabStop = false;
        ValueChanged += (_, _) => AnimateValue();
        Unloaded += (_, _) => SnapToValue();
    }

    protected override void OnApplyTemplate()
    {
        base.OnApplyTemplate();
        indicator = GetTemplateChild("Indicator") as ShapePath;
        SweepAngle = TargetAngle;
        Draw();
    }

    private double TargetAngle => Maximum > Minimum
        ? 360 * Math.Clamp((Value - Minimum) / (Maximum - Minimum), 0, 1) : 0;

    private void SnapToValue()
    {
        animation?.Stop();
        animation = null;
        SweepAngle = TargetAngle;
    }

    private void AnimateValue()
    {
        var from = SweepAngle;
        var target = TargetAngle;
        if (!IsLoaded || target == 0 || !uiSettings.AnimationsEnabled || Math.Abs(from - target) < 0.01)
        {
            SnapToValue();
            return;
        }
        animation?.Stop();
        SweepAngle = from;
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
        Storyboard.SetTargetProperty(transition, nameof(SweepAngle));
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
        var angle = Math.Clamp(SweepAngle, 0, 360);
        indicator.Visibility = angle > 0 ? Visibility.Visible : Visibility.Collapsed;
        // Two arcs also represent a completely full ring without a seam at 360 degrees.
        Point At(double degrees)
        {
            var radians = degrees * Math.PI / 180;
            return new Point(52 + 46 * Math.Sin(radians), 52 - 46 * Math.Cos(radians));
        }
        var figure = new PathFigure { StartPoint = At(0), IsClosed = false, IsFilled = false };
        foreach (var end in new[] { angle / 2, angle })
            figure.Segments.Add(new ArcSegment
            {
                Point = At(end), Size = new Size(46, 46),
                SweepDirection = SweepDirection.Clockwise, IsLargeArc = false
            });
        var geometry = new PathGeometry();
        geometry.Figures.Add(figure);
        indicator.Data = geometry;
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
