"""Markdown 转换前的 HTML 净化：不允许引用本机文件或网络资源，不保留脚本与样式。"""

from redactx.convert import _SANDBOX_SH, _sanitize


def test_sanitize_drops_local_and_remote_resources():
    out = _sanitize('<p>正文<img src="file:///data/jobs.sqlite3" alt="x"><img src="http://example.com/a.png"></p>'
                    '<iframe src="http://example.com">内</iframe><link rel="stylesheet" href="http://example.com/a.css">')
    assert "file:" not in out and "http:" not in out and "iframe" not in out and "link" not in out
    assert "[图片：x]" in out and "[图片]" in out


def test_sanitize_drops_script_and_style_content():
    out = _sanitize("<script>alert(1)</script><style>body{background:url(file:///etc/passwd)}</style><p>留下</p>")
    assert out == "<p>留下</p>"


def test_sanitize_keeps_markdown_structure_and_data_images():
    src = ('<h1>标题</h1><table><tr><td align="right">6.1</td></tr></table><pre><code>a &lt; b</code></pre>'
           '<img src="data:image/png;base64,AAAA" alt="图"><a href="file:///etc/passwd" onclick="x()">链接</a>')
    out = _sanitize(src)
    assert "<h1>标题</h1>" in out and '<td align="right">6.1</td>' in out and "a &lt; b" in out
    assert 'src="data:image/png;base64,AAAA"' in out
    assert "<a>链接</a>" in out  # 链接地址与事件属性都去掉


def test_sandbox_hides_data_dir_after_binding_workdir():
    # 先绑定工作目录，再用 tmpfs 盖住数据目录，顺序不能反
    assert _SANDBOX_SH.index("mount --bind") < _SANDBOX_SH.index("mount -t tmpfs")
