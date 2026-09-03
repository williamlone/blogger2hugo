#!/usr/bin/env python3
import os, re, sys
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "blogger": "http://schemas.google.com/blogger/2018"
}

# =====================================================
# Utility
# =====================================================

def safe_mkdir(path):
    """安全创建目录：若目录不存在则递归创建，已存在则跳过。"""
    if not os.path.isdir(path):
        os.makedirs(path)

def extract_text(element, tag, ns=ATOM_NS, default=""):
    """从 XML 元素中提取指定标签的文本内容；若标签不存在或为空，则返回默认值。"""
    el = element.find(tag, ns)
    return el.text.strip() if el is not None and el.text is not None else default

# =====================================================
# Tables Markdown Conversion
# =====================================================

def has_complex_table(table_tag):
    """检测表格是否含 colspan 或 rowspan 等复杂合并单元格；存在则返回 True。"""
    for cell in table_tag.find_all(["td","th"]):
        if cell.has_attr("colspan") or cell.has_attr("rowspan"):
            return True
    return False

def row_to_text_cells(row_tag):
    """将表格行（tr）中的所有单元格文本提取为列表，并对竖线字符做转义处理。"""
    cells = []
    for c in row_tag.find_all(["th","td"], recursive=False):
        txt = " ".join([s.strip() for s in c.stripped_strings])
        txt = txt.replace("|", "\\|")
        cells.append(txt)
    return cells

def table_to_markdown(table_tag):
    """将 HTML 表格转换为 Markdown 表格文本；若表格过于复杂（含合并单元格）或为空则返回 None。"""
    if has_complex_table(table_tag):
        return None
    rows = []
    for tr in table_tag.find_all("tr"):
        rows.append(row_to_text_cells(tr))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return None
    col_count = max(len(r) for r in rows)
    def norm(r):
        while len(r) < col_count:
            r.append("")
        return r
    rows = [norm(r) for r in rows]
    header = rows[0]
    body = rows[1:]
    md = (
        "\n\n| " + " | ".join(header) + " |\n" +
        "| " + " | ".join("---" for _ in header) + " |\n"
    )
    for r in body:
        md += "| " + " | ".join(r) + " |\n"
    return md + "\n\n"

def convert_tables(soup):
    """遍历 HTML 中所有 table 标签，能转 Markdown 的就替换，不能转的则保留原 HTML。"""
    for table in soup.find_all("table"):
        md = table_to_markdown(table)
        table.replace_with(NavigableString(md if md else "\n\n"+str(table)+"\n\n"))

# link conversion

def convert_links(soup):
    """将 HTML 中的 a 标签转换为 Markdown 链接格式 [文本](链接)；空链接或 JS 链接则跳过。"""
    for a in soup.find_all("a"):
        href = a.get("href")
        text = a.get_text(strip=True)
        if not href:
            continue
        # Skip iframed embeds or empty text
        if text == "" or href.startswith("javascript:"):
            continue
        a.replace_with(NavigableString(f"[{text}]({href})"))

# =====================================================
# HTML Cleaning + Media + Embed
# =====================================================

def sanitize_filename(filename):
    """规范化文件名：转小写、统一分隔符为连字符、去除非法字符、合并重复连字符。"""
    filename = filename.lower().replace("%20", "-").replace(" ", "-").replace("_", "-")
    filename = re.sub(r"[^a-z0-9\.-]+", "-", filename)
    return re.sub(r"-{2,}", "-", filename).strip("-")

def clean_html(html):
    """清理 HTML 内容：去除多余属性、将 img 转为 Markdown 图片（保留原 URL）、转换 iframe 嵌入（YouTube/Google Drive）、转换表格与链接，返回处理后的 HTML 字符串。"""
    soup = BeautifulSoup(html or "", "html.parser")

    # remove unnecessary attributes
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr not in ["src", "href", "alt", "title"]:
                del tag[attr]

    # === convert images to markdown, keep original URLs ===
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src:
            img.decompose()
            continue
        alt = img.get("alt", "")
        img.replace_with(NavigableString(f"![{alt}]({src})"))

    # iframe conversions
    for iframe in list(soup.find_all("iframe")):
        src = iframe.get("src", "")
        if not src:
            iframe.decompose()
            continue
        yt = re.search(r"(?:youtube\.com/embed/|youtu\.be/)([\w-]+)", src)
        if yt:
            iframe.replace_with(NavigableString(f"{{{{< youtube {yt.group(1)} >}}}}"))
            continue
        gd = re.search(r"drive\.google\.com/file/d/([^/]+)", src)
        if gd:
            iframe.replace_with(NavigableString(
                f"[Download PDF](https://drive.google.com/uc?export=download&id={gd.group(1)})"
            ))
            continue
        iframe.replace_with(NavigableString(str(iframe)))

    convert_tables(soup)
    convert_links(soup)
    return str(soup)

def html_to_markdown(html):
    """使用 markdownify 将 HTML 转为 Markdown；转换失败或结果为空时回退返回原始 HTML。"""
    try:
        md = markdownify(html, heading_style="ATX", strip=['a'])
        return md if md.strip() else html
    except Exception:
        return html

# =====================================================
# Frontmatter
# =====================================================

def frontmatter(title, date, updated, tags_yaml, permalink, draft_flag):
    """生成 Hugo 文章的 YAML frontmatter 头部（标题、日期、标签、别名、草稿标记等）。"""
    title = title.replace('"', '\\"')  # escape quotes to prevent YAML break
    aliases = f"url: /{permalink}\n" if permalink else ""
    return (
        "---\n"
        f'title: "{title}"\n'
        f"date: {date}\n"
        f"lastmod: {updated}\n"
        f"tags: {tags_yaml}\n"
        f"{aliases}"
        f"draft: {draft_flag}\n"
        "---\n\n"
    )

# =====================================================
# Main
# =====================================================

def convert_atom(atom_file, output_dir):
    """主转换函数：解析 Blogger 导出的 Atom XML，逐篇文章提取标题、日期、永久链接、内容等，清理 HTML 并转 Markdown，最后直接写入 Hugo 内容目录下的 {slug}.md 文件，并打印转换统计。"""
    tree = ET.parse(atom_file)
    root = tree.getroot()
    base_dir = os.path.join(output_dir, "posts")
    safe_mkdir(base_dir)

    count_posted = 0
    count_draft = 0

    for entry in root.findall("atom:entry", ATOM_NS):
        if extract_text(entry, "blogger:type") != "POST":
            continue

        status = extract_text(entry, "blogger:status")
        draft_flag = "true" if status == "DRAFT" else "false"

        # === Count draft vs posted ===
        if draft_flag == "true":
            count_draft += 1
        else:
            count_posted += 1

        title = extract_text(entry, "atom:title", ATOM_NS, "untitled")
        published = extract_text(entry, "atom:published", ATOM_NS)
        updated = extract_text(entry, "atom:updated", ATOM_NS)
        permalink = extract_text(entry, "blogger:filename", ATOM_NS).strip("/")

        # === Fallback if permalink missing ===
        if not permalink or permalink.strip() == "":
            title_fallback = extract_text(entry, "atom:title", ATOM_NS, "untitled")
            if not title_fallback.strip():
                # fallback to ID if title empty
                title_fallback = extract_text(entry, "atom:id", ATOM_NS).split("-")[-1]
            permalink = sanitize_filename(title_fallback.lower().replace(" ", "-")) + ".html"

        slug = sanitize_filename(re.sub(r"\.html$", "", permalink))

        html = extract_text(entry, "atom:content", ATOM_NS, "")
        cleaned = clean_html(html)
        markdown = html_to_markdown(cleaned)

        tags = [t.attrib.get("term") for t in entry.findall("atom:category", ATOM_NS) if t.attrib.get("term")]
        tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]"

        post_file = os.path.join(base_dir, f"{slug}.md")
        with open(post_file, "w", encoding="utf-8") as f:
            f.write(frontmatter(title, published, updated, tags_yaml, permalink, draft_flag))
            f.write(markdown)

        print(f"[OK] /posts/{slug}.md | draft={draft_flag}")

    # === Final summary ===
    print(f"\n🎉 Completed extract {count_posted} posted article(s) and {count_draft} draft article(s)!\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python blogger2hugo.py [file.atom] [output_dir]")
        sys.exit(1)
    convert_atom(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "content")

