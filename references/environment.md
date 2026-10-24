# 环境与本地配置

核心脚本使用Python、FFmpeg、OpenCV、NumPy和Pillow。建议Python 3.10—3.12：

```sh
python -m pip install -r requirements.txt
python scripts/meeting.py doctor
```

FFmpeg另行安装并加入PATH。Windows使用系统简体中文OCR语言包；非Windows使用Tesseract及chi_sim、eng语言数据。OCR处理在本地进行，需自行检查公式和误识别。

PDF原件用PyMuPDF提取文字并渲染。PPTX文字与备注按实际演示顺序提取；图片渲染默认调用已安装的LibreOffice（soffice），也可配置已有的Node/artifact运行时。仓库不分发第三方运行时、字体、Office模板或二进制。

默认报告、图片和人物名片不需要Node。可选PPTX导出需要node_modules与已安装的presentations技能路径，以执行其结构和导入检查；无此依赖可使用图片或PDF输出。

配置只在本地保存，例如复制runtime.example.json为项目的.local/runtime.json，再填自己的依赖路径：

```sh
python scripts/meeting.py --config .local/runtime.json doctor
python scripts/meeting.py --config .local/runtime.json prepare '腾讯会议链接' --out meetings/example
```

示例里的null表示不启用该可选配置。常规pip安装无需cv_deps；只有使用另一处已有包目录时才设置。work_root控制自动查找和新建会议目录的位置；也可用全局--work-root或prepare的--out指定。vault仅在明确需要入库时配置，或在finish时用--vault传入。

脚本不会收集浏览器登录态或自动读取Cookie。下载器可接收用户显式提供的Cookie或storage state，这些文件及媒体签名均属私有运行数据，不要提交。
