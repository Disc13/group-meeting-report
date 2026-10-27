# Group Meeting Report Skill

把获准访问的腾讯会议回放，整理成**按人、逐页插图的发言润色稿**：保留讲述逻辑与实验条件，解释难概念，归纳中途和末尾提问，再整理导师开场与总结。另生成研究名片，可选积累到Obsidian。

固定脚本处理下载、平台字稿、取页、OCR、原页匹配、时间对齐和鼠标候选；Agent负责理解与润色。默认交付图片和Markdown，不经过PDF/PPTX中转。没有原件可从录像取页，有PPT/PDF则直接渲染并匹配。

## 安装与调用

这是一个可独立克隆的Codex Skill仓库。将仓库放到自己的skills目录下，文件夹名为group-meeting-report；不要覆盖已有的个人版本。也可以在其他支持SKILL.md的Agent中使用，按宿主的技能安装方法配置。

```sh
git clone https://github.com/Disc13/group-meeting-report.git
cd group-meeting-report
python -m pip install -r requirements.txt
python scripts/meeting.py doctor
```

依赖FFmpeg与固定OCR；PPTX渲染另需LibreOffice或已安装的artifact运行时。详见[环境与配置](references/environment.md)。仓库不捆绑第三方运行时或私人模板。

在Agent中调用：

```text
使用 $group-meeting-report 整理这个腾讯会议链接，
生成逐页图文讲稿、现场问答、导师发言和人物研究名片。
有需要时写入我指定的Obsidian知识库。
```

附回放链接，PPT/PDF可选。首次只需配置自己的依赖和工作目录，不需要本仓库作者的机器路径或知识库。

## 流程与命令

1. **准备材料。** 获取现有平台字稿，下载共享屏幕或使用指定录像，生成图片、OCR、对齐材料及稀疏指针候选。

   ```sh
   python scripts/meeting.py prepare 'https://meeting.tencent.com/cw/DemoShareId000' --out meetings/example
   ```

   DemoShareId000是虚构占位示例，需替换成获准使用的链接。可加`--deck deck.pdf`或`--video recording.mp4`。同一链接可复用缓存。

2. **Agent整理。** 读RUN_AGENT.md与原材料，核对实际顺序、身份和短章节，写结构与选页，执行split_meeting.py。每人先介绍主题，再按每页实际发言插图润色、解释概念。问答逐项写“现场问题—现场回应—AI补充解答（非现场发言）”：AI直接回答并解释依据，需要核查的结论引用原论文或官方资料，不能把补充写成人物成果或导师决定。导师开场和结束发言单列，不另做行动清单。

3. **逐页检查、按需编号。** 导出图片后用review_references.py init建立内部检查记录，逐页判断关键指代。查需要定位的语句附近的鼠标候选，核对证据帧后由annotate_slides.py生成编号副本，正文用①②引用。每页记录已标注、无需标注或证据不足；不设标注配额，不猜位置。检查统计与原因只在交付对话中汇报，报告只放必要编号图和讲解。命令及JSON字段见[数据契约](references/workflow-contract.md)。

4. **完成与可选入库。** Agent写好组会报告.md及人物汇报提要.json后：

   ```sh
   python scripts/meeting.py finish meetings/example
   python scripts/meeting.py finish meetings/example --vault '你的知识库目录'
   ```

   第一条校验逐页检查记录、生成图片集与名片，第二条检查并写入显式指定的Obsidian目录。待审页、失效记录或缺失的编号图引用会阻止交付。组会报告与人物互链，同人跨会议累积，手工补充保留。检查可用`--check-only`，重建图片用`--rebuild-slides`。

图片按人保存，报告直接引用原图或编号PNG。Obsidian使用库内路径原生嵌入。PDF和截图PPTX是可选导出；截图不恢复可编辑公式或图表对象。

自动材料包保留不同capture，只有同一capture再次出现或可靠匹配到同一原件页时自动合并。相似OCR与相同页眉不证明同页，少字图表保留并提示审阅；动画、回翻由Agent核对后用families归并。这样可保留版式相同但结果不同的实验页。

## 隐私与许可

本仓库只发布通用代码、规则和合成测试，**不含真实会议、人物、演示文件、截图或私人配置**。公开版是独立项目，不包含私人版本的历史；本地完整版不被替换。

运行生成的字稿、报告、媒体、原始响应、签名链接和身份信息仍可能敏感。源码采用默认忽略的文件白名单；会议目录、本地配置和缓存不应加入Git。完整范围与Agent模型调用边界见[隐私说明](references/privacy.md)。

代码采用[MIT](LICENSE)许可。外部工具和依赖保留各自许可；会议材料不因本项目开源而获得公开授权。

## 验证与当前边界

```sh
python scripts/run_tests.py
python scripts/tmdown.py --selftest
```

测试使用合成图像、虚构人物和本地HTTP服务，不请求真实会议、不读取私人材料。它们覆盖下载续传、缓存隔离、几何配准、逐人归属、直接图片导出、原生嵌入与手改保护，不证明研究结论或OCR完全正确。

公开版还用两页合成PDF、8秒合成视频及离线模拟字稿跑过准备材料、OCR、原页匹配、图片导出、报告渲染、人物名片和Obsidian入库；重复入库没有新增改动。真实会议内容不作为公开测试材料。

- 当前读取腾讯会议已有字稿；字稿不完整或不可用时明确停止，尚无自动ASR回退。
- 平台前端接口未公开保证稳定，访问或分享限制需要合法解决。
- 默认2秒抽帧，极短页和动画中间状态可能遗漏。未提供原件时不保证完整恢复。
- 鼠标是稀疏CV候选，尚未建立全场真值准确率、连续轨迹或点击恢复。
- 原件入口一次接一份PPT/PDF，其余汇报继续用录像；多份原件需分别导入并确认归属。
- Agent写作仍是流程的一部分；prepare完成不等于报告已完成。
