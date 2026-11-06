using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;

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
            var text = string.Join(" ", paragraph);
            var block = new TextBlock { Style = (Style)Resources["ReadmeText"] };
            AddHelpInlines(block, text);
            section!.Children.Add(block);
            paragraph.Clear();
        }
        for (var index = 0; index < lines.Length; index++)
        {
            var line = lines[index].Trim();
            if (line == "## Сборка") { Flush(); break; }
            if (line.StartsWith("<img ") || line.StartsWith("# ")) continue;
            if (index + 1 < lines.Length && line.Contains('|') && IsHelpTableDivider(lines[index + 1]))
            {
                Flush();
                if (section is null) AddSection("О проекте");
                var rows = new List<string[]> { HelpTableCells(line) };
                index++;
                while (index + 1 < lines.Length && lines[index + 1].Trim().Contains('|') && lines[index + 1].Trim().Length > 0)
                    rows.Add(HelpTableCells(lines[++index]));
                section!.Children.Add(CreateHelpTable(rows));
            }
            else if (line.StartsWith("```"))
            {
                Flush();
                if (section is null) AddSection("О проекте");
                var code = new List<string>();
                while (++index < lines.Length && !lines[index].TrimStart().StartsWith("```"))
                    code.Add(lines[index]);
                section!.Children.Add(new TextBlock
                {
                    Text = string.Join("\n", code), Style = (Style)Resources["ReadmeText"],
                    FontFamily = new FontFamily("Consolas")
                });
            }
            else if (Regex.IsMatch(line, @"^#{2,6} "))
            {
                Flush();
                var heading = line.StartsWith("## ");
                AddSection(line.TrimStart('#', ' '), heading);
            }
            else if (line.Length == 0) Flush();
            else if (Regex.IsMatch(line, @"^(?:[-+*]|\d+[.)]) "))
            {
                Flush();
                if (section is null) AddSection("О проекте");
                var item = Regex.Match(line, @"^(?<marker>[-+*]|\d+[.)]) (?<text>.*)$");
                var block = new TextBlock { Style = (Style)Resources["ReadmeText"], Margin = new Thickness(8, 0, 0, 0) };
                var marker = item.Groups["marker"].Value;
                block.Inlines.Add(new Run { Text = marker is "-" or "+" or "*" ? "• " : marker + " " });
                AddHelpInlines(block, item.Groups["text"].Value);
                section!.Children.Add(block);
            }
            else if (!line.StartsWith("> [!")) paragraph.Add(line.StartsWith("> ") ? line[2..] : line);
        }
        Flush();
    }

    private static string[] HelpTableCells(string line) =>
        Regex.Split(line.Trim().Trim('|'), @"(?<!\\)\|").Select(cell => cell.Trim().Replace(@"\|", "|")).ToArray();

    private static bool IsHelpTableDivider(string line)
    {
        var cells = HelpTableCells(line);
        return cells.Length > 0 && cells.All(cell => Regex.IsMatch(cell, @"^:?-{3,}:?$"));
    }

    private Grid CreateHelpTable(IReadOnlyList<string[]> rows)
    {
        var table = new Grid { ColumnSpacing = 18, RowSpacing = 12 };
        var columns = rows[0].Length;
        for (var column = 0; column < columns; column++)
            table.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        for (var row = 0; row < rows.Count; row++)
        {
            table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            for (var column = 0; column < columns; column++)
            {
                var cell = new TextBlock { Style = (Style)Resources["ReadmeText"] };
                if (row == 0) cell.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
                if (column < rows[row].Length) AddHelpInlines(cell, rows[row][column]);
                Grid.SetRow(cell, row);
                Grid.SetColumn(cell, column);
                table.Children.Add(cell);
            }
        }
        return table;
    }

    private static void AddHelpInlines(TextBlock block, string markdown) => AddHelpInlines(block.Inlines, markdown);

    private static void AddHelpInlines(InlineCollection inlines, string markdown)
    {
        var offset = 0;
        foreach (Match token in Regex.Matches(markdown,
            @"`(?<code>[^`]+)`|\[(?<label>[^\]]+)\]\((?<url>[^\s)]+)\)|\*\*(?<bold>.+?)\*\*|__(?<bold>.+?)__|(?<!\*)\*(?<italic>[^*]+)\*(?!\*)"))
        {
            inlines.Add(new Run { Text = markdown[offset..token.Index] });
            if (token.Groups["code"].Success)
                inlines.Add(new Run { Text = token.Groups["code"].Value, FontFamily = new FontFamily("Consolas") });
            else if (token.Groups["label"].Success)
            {
                var target = token.Groups["url"].Value;
                if (Uri.TryCreate(new Uri("https://github.com/ytrbqdkflbvbhy/foxbridge/blob/main/"), target, out var uri)
                    && uri.Scheme is "https" or "http")
                {
                    var hyperlink = new Hyperlink { NavigateUri = uri };
                    AddHelpInlines(hyperlink.Inlines, token.Groups["label"].Value);
                    inlines.Add(hyperlink);
                }
                else inlines.Add(new Run { Text = token.Groups["label"].Value });
            }
            else
            {
                var span = new Span();
                if (token.Groups["bold"].Success) span.FontWeight = Microsoft.UI.Text.FontWeights.Bold;
                else span.FontStyle = Windows.UI.Text.FontStyle.Italic;
                AddHelpInlines(span.Inlines, token.Groups[token.Groups["bold"].Success ? "bold" : "italic"].Value);
                inlines.Add(span);
            }
            offset = token.Index + token.Length;
        }
        inlines.Add(new Run { Text = markdown[offset..] });
    }
}
