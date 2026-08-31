/**
 * MarkdownMessage — renders AI text without raw Markdown syntax.
 *
 * Strategy:
 * - Detect and render Markdown tables as <table> elements
 * - Render ## / ### headings as <h3>/<h4>
 * - Render **bold** and *italic* inline
 * - Render --- horizontal rules
 * - Render unordered list items
 * - Plain paragraphs get <p> tags
 *
 * No external library required.
 */

function parseInline(text) {
    // Bold+italic ***text***, then bold **text**, then italic *text* or _text_
    return text
        .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/_(.+?)_/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>');
}

function renderCell(text) {
    return { __html: parseInline(text.trim()) };
}

function parseTable(lines) {
    // lines[0] = header row, lines[1] = separator, lines[2+] = data rows
    const parseRow = (line) =>
        line.replace(/^\||\|$/g, '').split('|').map(c => c.trim());

    const headers = parseRow(lines[0]);
    const rows = lines.slice(2).map(parseRow);

    return (
        <div className="ai-md-table-wrap" key={lines[0]}>
            <table className="ai-md-table">
                <thead>
                    <tr>
                        {headers.map((h, i) => (
                            <th key={i} dangerouslySetInnerHTML={renderCell(h)} />
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, ri) => (
                        <tr key={ri}>
                            {row.map((cell, ci) => (
                                <td key={ci} dangerouslySetInnerHTML={renderCell(cell)} />
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function MarkdownMessage({ content }) {
    if (!content) return null;

    const lines = content.split('\n');
    const blocks = [];
    let i = 0;

    while (i < lines.length) {
        const line = lines[i];

        // Blank line
        if (!line.trim()) { i++; continue; }

        // Horizontal rule
        if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
            blocks.push(<hr key={i} className="ai-md-rule" />);
            i++;
            continue;
        }

        // Heading ###
        const h3 = line.match(/^###\s+(.*)/);
        if (h3) {
            blocks.push(<h4 key={i} className="ai-md-h4" dangerouslySetInnerHTML={{ __html: parseInline(h3[1]) }} />);
            i++;
            continue;
        }

        // Heading ##
        const h2 = line.match(/^##\s+(.*)/);
        if (h2) {
            blocks.push(<h3 key={i} className="ai-md-h3" dangerouslySetInnerHTML={{ __html: parseInline(h2[1]) }} />);
            i++;
            continue;
        }

        // Heading #
        const h1 = line.match(/^#\s+(.*)/);
        if (h1) {
            blocks.push(<h3 key={i} className="ai-md-h3" dangerouslySetInnerHTML={{ __html: parseInline(h1[1]) }} />);
            i++;
            continue;
        }

        // Markdown table — look ahead for separator row
        if (line.startsWith('|') && lines[i + 1] && /^\|[-\s|:]+\|/.test(lines[i + 1])) {
            const tableLines = [line];
            i++;
            while (i < lines.length && lines[i].startsWith('|')) {
                tableLines.push(lines[i]);
                i++;
            }
            blocks.push(parseTable(tableLines));
            continue;
        }

        // Bullet list item
        const bullet = line.match(/^[-*]\s+(.*)/);
        if (bullet) {
            const items = [];
            while (i < lines.length && lines[i].match(/^[-*]\s+(.*)/)) {
                const m = lines[i].match(/^[-*]\s+(.*)/);
                items.push(<li key={i} dangerouslySetInnerHTML={{ __html: parseInline(m[1]) }} />);
                i++;
            }
            blocks.push(<ul key={`ul-${i}`} className="ai-md-list">{items}</ul>);
            continue;
        }

        // Numbered list item
        const numbered = line.match(/^\d+\.\s+(.*)/);
        if (numbered) {
            const items = [];
            while (i < lines.length && lines[i].match(/^\d+\.\s+(.*)/)) {
                const m = lines[i].match(/^\d+\.\s+(.*)/);
                items.push(<li key={i} dangerouslySetInnerHTML={{ __html: parseInline(m[1]) }} />);
                i++;
            }
            blocks.push(<ol key={`ol-${i}`} className="ai-md-list">{items}</ol>);
            continue;
        }

        // Plain paragraph — accumulate consecutive non-special lines
        const paraLines = [];
        while (
            i < lines.length &&
            lines[i].trim() &&
            !lines[i].startsWith('#') &&
            !lines[i].startsWith('|') &&
            !lines[i].match(/^[-*]\s+/) &&
            !lines[i].match(/^\d+\.\s+/) &&
            !/^---+$/.test(lines[i].trim())
        ) {
            paraLines.push(lines[i]);
            i++;
        }
        if (paraLines.length) {
            const combined = paraLines.join(' ');
            blocks.push(
                <p key={`p-${i}`} className="ai-md-p" dangerouslySetInnerHTML={{ __html: parseInline(combined) }} />
            );
        }
    }

    return <div className="ai-md-body">{blocks}</div>;
}
