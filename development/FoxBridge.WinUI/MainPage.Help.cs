using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;

namespace FoxBridge_WinUI;

public sealed partial class MainPage
{
    private void InitializeReadme()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Help", "README.md");
        string[] lines;
        try { lines = File.ReadAllLines(path); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            ReadmeSections.Children.Add(new TextBlock { Text = "Файл справки Help/README.md недоступен. Восстановите полную поставку приложения.", TextWrapping = TextWrapping.Wrap });
            return;
        }
        StackPanel? section = null;
        var paragraph = new List<string>();
        void AddSection(string title, bool heading = false)
        {
            section = new StackPanel { Spacing = 12 };
            section.Children.Add(new TextBlock { Text = title, FontSize = heading ? 20 : 18,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
            ReadmeSections.Children.Add(new Border { Style = (Style)Resources["Card"], Child = section });
        }
        void Flush()
        {
            if (paragraph.Count == 0) return;
            if (section is null) AddSection("О проекте");
            var list = paragraph.Any(line => line.StartsWith("* ") || Regex.IsMatch(line, @"^\d+\. "));
            var text = string.Join(list ? "\n" : " ", paragraph.Select(line => line.StartsWith("* ") ? "• " + line[2..] : line));
            var block = new TextBlock { Style = (Style)Resources["ReadmeText"] };
            AddHelpInlines(block, text);
            section!.Children.Add(block);
            paragraph.Clear();
        }
        foreach (var raw in lines)
        {
            var line = raw.Trim();
            if (line == "## Сборка") { Flush(); break; }
            if (line.StartsWith("<img ") || line.StartsWith("# ")) continue;
            if (line.StartsWith("## ") || line.StartsWith("### "))
            {
                Flush();
                var heading = line.StartsWith("## ");
                AddSection(line.TrimStart('#', ' '), heading);
            }
            else if (line.Length == 0) Flush();
            else if (!line.StartsWith("> [!")) paragraph.Add(line.StartsWith("> ") ? line[2..] : line);
        }
        Flush();
    }

    private static void AddHelpInlines(TextBlock block, string markdown)
    {
        var offset = 0;
        static string Plain(string value) => value.Replace("`", "").Replace("**", "");
        foreach (Match link in Regex.Matches(markdown, @"\[(?<label>[^\]]+)\]\((?<url>https?://[^\s)]+)\)"))
        {
            block.Inlines.Add(new Run { Text = Plain(markdown[offset..link.Index]) });
            var hyperlink = new Hyperlink { NavigateUri = new Uri(link.Groups["url"].Value) };
            hyperlink.Inlines.Add(new Run { Text = link.Groups["label"].Value });
            block.Inlines.Add(hyperlink);
            offset = link.Index + link.Length;
        }
        block.Inlines.Add(new Run { Text = Plain(markdown[offset..]) });
    }
}
