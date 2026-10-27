# 输入、输出与整理契约

## 材料

prepare获取分享页与平台字稿，下载共享屏幕轨道或复用显式指定的录像。cw和crm的同一短码视为同一会议；目录名不是会议日期。无完整字稿时保存实际返回状态并停止，不生成无依据的整场发言。

主要材料包括manifest.json、transcript.md/jsonl、captures/slides.json、reading-order.json、aligned.jsonl和material-packet.md。原件导入目录保存deck.json、文字和逐页PNG。platform speaker标签不等于核实过的人物身份。

## meeting-structure.json

presentations按实际汇报顺序排列。下面仅为合成字段示例：

```json
{
  "presentations": [{
    "id": "presenter-a",
    "presenter": "汇报者A",
    "title": "示例研究进展",
    "kind": "研究进展",
    "file_stem": "01-presenter-a",
    "material_start_s": 0,
    "material_end_s": 120,
    "talk_start_s": 5,
    "talk_end_s": 100,
    "discussion_start_s": 100,
    "discussion_end_s": 120
  }],
  "advisor_summary": {"speaker": "导师", "start_s": 120, "end_s": 150}
}
```

时间为录像起点秒数。材料窗连续、不重叠，并包含本人的问答回翻；分组按所有实际展示区间，不能只按一张代表图首次出现的时间。可加reference_dir指定原件目录，interruptions记录中途提问，advisor_opening记录已收录的开场。日期应有材料依据，不明时注明，不用处理当天冒充。

## 逐页图片

select_images.py生成captures/image-selection.json候选。材料包只自动合并同一capture的再次出现或已验证匹配到同一原件页的候选，不按OCR相似度或相同页眉合并不同页面；少字图表进入needs_review而不直接删除。Agent核对短章节、编辑器重复和动态演示后，写included列表、reviewed:true以及当前slides.json的SHA256。元数据改变后重审。families仅将核对后确认同一页的其他capture ID归入代表页，以保留那些时间的发言。

```sh
python scripts/split_meeting.py meetings/example
```

output/images/按人保存图片与索引，sections/*.json保留全部区间。原件匹配成功时直接用原件渲染图，否则用录像图。原文件保留，不以恢复页数证明未提供原件的PPT完整。--pdf、--pptx为可选导出。

## 鼠标编号

```sh
python scripts/annotate_slides.py query meetings/example/captures '00:00:10' --window 4
python scripts/annotate_slides.py render meetings/example/captures meetings/example/annotation-plan.json
```

先查附近的候选和句子，按需查看该时刻采样帧，确认指代后写计划。以下编号和时间也完全是示例：

```json
{"pages":[{"page_id":"capture-001","points":[
  {"number":1,"time_s":10,"utterance_id":"demo-utterance","reviewed":true,"label_offset":[50,12]}
]}]}
```

reviewed表示Agent已核对该帧与语句，不要求用户逐点批准。脚本检查唯一候选、语句时间、页面家族、ORB/RANSAC几何一致性，再画编号和引线。label_offset只移动号码，不改变证据坐标。原图保留，annotations.json记录时间、句子、坐标、配准统计和来源哈希。候选不足时留缺，不猜位置或推断点击、身份。

## 内部指代检查与对话交付

选页和分人图片导出完成后执行：

```sh
python scripts/review_references.py init meetings/example
# Agent逐页填写reference-review.json，按需生成编号图并引用
python scripts/review_references.py check meetings/example
```

init生成材料哈希和每个`(presentation_id, page_id)`的待审条目。保留生成的inputs，仅填写pages的判断；以下时间、句子与页ID均为虚构示例：

```json
{"presentation_id":"speaker-a","page_id":"capture-001",
 "status":"annotated","reason":"两条曲线需要明确区分。",
 "evidence":[{"time_s":10,"utterance_id":"demo-utterance"}]}
```

status为`annotated`、`unnecessary`或`insufficient_evidence`，reason必须说明本页的具体依据。对复杂图、多曲线及含混口头指代实际审阅；需要定位但证据不足时选insufficient_evidence，不能冒充无需标注。所有页面都要有判断，不设最低标注数量；无需定位的页可留空evidence。其余两类需给本汇报段对应页面的时间与字稿ID，已标注页列全所用编号点的时间/句子。

check与finish验证条目覆盖、材料哈希、时间归属、编号源图/输出/采样帧哈希，以及最终本地报告中的图片顺序与编号解释。报告在入库前使用本地Markdown图片路径；编号1至20用①至⑳，较大的号用“编号21”等文字引用。已标注项必须真正生成`captures/annotated/annotations.json`及PNG，并在对应图片后的讲解中引用，不能仅写一个计划。源图或编号图变化后重新render；字稿、选页、结构或指针数据变化后重新审阅，用init --reset将旧决定清回待审。该检查核对产物一致性，不证明语义判断正确。

内部reference-review.json留在会议工作目录，脚本不会将它写入报告或Obsidian正文。finish返回annotation_review_for_chat，Agent在**对话中**简述三类数量、代表页/时间及重要未标注原因；报告只呈现必要编号图和讲解，不放检查统计、清单或未标注原因附录。既有报告不会自动补改，重新finish时按新要求审阅。

## 报告与人物提要

组会报告.md每人包含简短介绍、逐页图文润色稿及中途/末尾问答，最后整理导师开场和结束发言。附录标题为`## 材料与核查范围`。也可写组会讲稿源稿.md，用`![页名](slide:capture-001)`引用图片，再用render_report.py解析实际路径并检查页序和覆盖。

每个问答议题依次包含现场问题（提问者与时间）、现场回应（回答及补充）、AI补充解答（非现场发言）。AI直接回答并解释依据，现场原意与外部纠正分开；需要核查的论文、争议或时效结论查原论文/官方资料并给来源，缺少数据时说明条件。AI段不冒充有录像时间的发言，不把建议或推断当作人物成果或导师决定。完整格式见scripts/agent-report-prompt.md，仍在每人问答内，不另增第三个主体章节。

人物汇报提要.json顶层字段：schema_version、meeting_id、meeting_date、updated、title、date_basis、presenters。每人字段如下：

- name、presentation_id：与结构一致。
- report_heading：正文真实二级标题，用于锚点。
- title、kind、directions：本次内容与有证据的研究主题。
- summary、progress：内容与当时完成状态。
- discussions、follow_up：字符串列表，保留提问及建议/计划/决定的区别；正文不另列行动表。
- evidence_window：录像区间。
- card.research、card.progress、card.open_question：各一至两句，说明研究内容、最近实绩和当前问题。

## 可选Obsidian入库

finish --vault将报告写入知识/组会、人物写入知识/人物、材料收入附件/组会。目录可用sync_obsidian.py的本地JSON配置调整。图片统一使用库内路径原生嵌入`![[附件/组会/日期/图/文件.png]]`，不用../../图片路径。

同场重复运行更新，同一人物跨会议累积。日期不是唯一录制身份，同日另一次会议有冲突时先解决，不覆盖。维护区块外的手工补充保留，区块内手改会要求合并。脚本不修改插件、不创建Git提交、不推送。视频、全量采样、OCR及接口原始响应保留在会议工作目录。
