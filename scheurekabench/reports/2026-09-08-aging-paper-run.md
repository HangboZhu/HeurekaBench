# Aging-Paper 出题全流程报告

日期：2026-09-07 ~ 2026-09-08
论文：`molintbench/Aging-Paper/nature-aging.pdf`（Nature Aging "News & Views" 评论，
Biswas & Stout 解读 Lei et al. 卵巢衰老研究：卵母细胞 mtDNA 泄漏 → cGAS–STING →
cGAMP 经 CX37 缝隙连接传播 → 颗粒细胞无菌炎症）

## 流程与产物

| 步骤 | 结果 |
|---|---|
| Step 1a 抽取 | 10 条 insights（claude_code，2.5 分钟） |
| Step 1b 辩论 | 2 轮互驳 + 裁决通过；终审因网关 `unrecognized_model` 抖动跳过（不影响） |
| Step 2 出题 | OE 20 题（泄漏 0）；MCQ 两次生成失败（网关返回 JSON 首部截断 / 尾部括号错位），括号平衡修复器从第二次输出修复出全部 20 题（泄漏 0） |
| Step 3 OE 闭环 | 双 solver（MiniMax-M3, glm-5.3-flash）错误并集，grader **gpt-5.6-sol**，3 轮 |

## 难度闭环分数（grader = gpt-5.6-sol，注意与 TCR 报告的 luna 口径略有差异）

| 轮次 | MiniMax-M3 | glm-5.3-flash | 整体 |
|---|---|---|---|
| 第 1 轮（初版） | ~4.35 | 4.70 | 4.53 |
| 第 2 轮（18/20 题变更） | 4.65 | 4.60 | 4.62 ⬆ |
| 第 3 轮（18/20 题变更）**← 定稿** | **4.20** | **4.42** | **4.31** |

定稿 = round3，20 题，自包含泄漏 0。距目标区间（2.5-3.5）还差 0.81。

## 观察

1. **错误并集在此论文上效果弱于 TCR**（两轮 -0.22 vs TCR 一轮 -0.82）：
   cGAS–STING / 干涉素 / 线粒体生物学是语料大户，题干自包含地把关键观察给足了，
   强模型"已知机制 + 题干事实"即可组装满分答案；初版错误集合单薄（glm 4.70 几乎无错可挖）。
2. 第 2 轮反而回弹（4.62）：重生成换题后暴露了新的知识可答面——内容侧问题，非措辞问题。
3. 与既有结论一致：**通识覆盖度决定天花板**。"News & Views 评论"虽比综述窄，但主题
   太热，专有事实密度不如预期的原创研究论文。

## 建议（下一步压分路径）

1. 对本题库再压：用 `--seed_solved` 复用 round3 结果，换 **gpt-5.6-terra / 三 solver 并集**
   跑 1-2 轮（更强的标尺暴露更多可强化点）；
2. 机制侧：落实"通识可答即打回"机器闸门（grader 先判"仅凭通识能否写出这些 facts"）；
3. 选材侧：优先**冷门系统 / 新方法学的原创研究论文**（专有数字、专有数据集密度最高）。

## 运维事件记录（对后续跑批有参考价值）

- 网关 `gpt-5.6-luna` 当晚开始返回空串（模型在列但账号组不服务）→ 切 `gpt-5.6-sol`；
  闭环熔断（判分全零即 SystemExit）正确拦截了第一次带病运行；
- claude_code CLI 偶发 `unrecognized_model "glm-5.3[1m]"`，内置重试可自愈；
- MCQ 结构化生成连续两次 JSON 损坏（首部截断 / 尾部括号错位）→ 建议把本次临时写的
  括号平衡修复器沉淀进 `insights_to_questions.extract_json` 兜底（待办）。

## 产物清单（`molintbench/Aging-Paper/`）

- 定稿：`oe_questions.json/.txt`（round3，4.31）、`mcq_questions.json/.txt`
- 过程：`insights.json`、`debate_transcript_claude_code.json`、
  `oe_questions_round{2,3}.json`、`oe_questions_solved_round{1,2,3}.json`、
  `mcq_repaired_items.json`、全程日志 `pipeline_20260907.log`
