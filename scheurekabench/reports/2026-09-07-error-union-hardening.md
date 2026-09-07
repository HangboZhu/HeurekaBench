# 错误并集强化（error-union hardening）：机制落地与首轮实战

日期：2026-09-06 ~ 2026-09-07
范围：`difficulty_loop.py` 机制升级 + TCR-T-paper OE 闭环第 3 次运行（双 solver）

---

## 1. 动机

9/5-9/6 的测量坐实了旧闭环的平台期：五轮"措辞加固"只能把 OE 均分从 4.80 压到 4.50，
且 grader 严格度不是杠杆。分析发现：能压住 solver 的题（Insight3/Q2 等）全是"采分点
绑定论文专有事实"的题；而 solver 被判 INCORRECT/PARTIAL 的 rubric facts 是**已证实的
错误认知**——直接拿来指导新题生成，比笼统的"出得更难"有效得多。

## 2. 机制（已写入 `difficulty_loop.py`，PIPELINE.md 5.2 同步更新）

1. **错误集合挖掘 `collect_errors()`**：每轮判分后，凡 solver 对某 rubric fact 判
   INCORRECT（直接推翻）或 PARTIAL（只 hedge 不表态）的，该 fact 原文 + solver 名逐条
   注入重生成 prompt 的 "PROVEN solver weak points" 区块；
2. **反向利用指令**：要求新题的采分点**直接反驳这些错误认知**——重复同样错误必
   INCORRECT/MISSING，列举式不表态最多 PARTIAL；
3. **多 solver 错误并集**：`--solver A,B` 时取所有 solver 的错误并集（避免只针对单一
   模型出题）；
4. **`--seed_solved SOLVER=PATH`**：把 `solve_and_grade.py` 的缓存结果直接作为第 1 轮
   成绩（不重做题、不重判分、计入均分）；
5. **健壮性**：每轮全部结果落盘 `<qtype>_questions_solved_round<N>.json`（分数不再只
   在终端）；claude_code 空输出重试一次。

## 3. 首轮实战结果（TCR-T-paper，OE 20 题，grader = gpt-5.6-luna）

| 轮次 | MiniMax-M3 | glm-5.3-flash | 整体（均值） |
|---|---|---|---|
| 第 1 轮（强化前，MiniMax 复用缓存） | 4.50 | 4.75 | 4.62 |
| 第 2 轮（一次错误并集强化，19/20 题变更） | 3.80 | 4.45 | 4.12 |
| 第 3 轮（二次强化，12/20 题变更）**← 定稿** | **3.40** | **3.85** | **3.62** |

- **两轮共降 1.00 分**（4.62 → 3.62）；旧机制同期仅 ~0.3。MiniMax 分布从
  5分×17 变为 1分×2、2分×4、3分×5、4分×2、5分×7——区分度曲线真正拉开了。
- 3.62 距目标区间上限（3.5）仅 0.12，在单次测量噪声（±0.2）内，基本达标。
- 第 1 轮重生成 10/10 insight 成功（9/5 旧机制 4 个空输出失败）。
- 定稿 = round3，回写 `oe_questions.json`/`.txt`，自包含泄漏扫描 **0**。

过程备注：闭环进程在第 3 轮测分中途死亡（疑似机器休眠），但第 1、2 轮结果均已落盘、
round3 快照完整，用 `solve_and_grade.py` 断点补测即恢复——新加的落盘机制直接回本。

## 4. 遗留观察

1. glm 第 3 轮仍偏高（3.85）：错误并集里 MiniMax 的弱点占多，新题对它杀伤偏小。
   后续可加入第三 solver（deepseek-v4-flash）扩大并集，或用 gpt-5.6-luna 当标尺。
2. 若还要再压：继续 1-2 轮强化（`--seed_solved` 复用 round3 结果，成本很低），或按
   9/6 报告的策略 2（采分点强制"专有绑定"+ 通识可答即重生成的机器闸门）。
3. MCQ 未做错误并集闭环（MCQ 无 fact 级判分，机制不适用；维持"基础理解检查"定位）。

## 5. 追加两轮验证（loop#4，round3 之上双 solver 种子续跑）

在 3.62 的定稿基础上再跑两轮（`--max-rounds 2`，双 solver 均以 round3 结果做种子，
第 1 轮免做题；错误并集首次纳入 glm 的全部弱点）：

| 轮次 | MiniMax-M3 | glm-5.3-flash | 整体 |
|---|---|---|---|
| 基准（= round3 定稿，种子复用） | 3.40 | 3.85 | 3.62 |
| 再强化一轮（12/20 题变更） | 3.50 | 4.05 | **3.77** ⬆ |

- **未再改善**：best-round 逻辑正确保留 3.62 的定稿不变（题目、泄漏 0 均未变）。
- 结论：错误并集机制的收益集中在首次应用（4.62 → 3.62，-1.00）；再往后换题 12 道也
  压不动了——剩余过易题的根子是"通识可答"，属于内容问题，重生成解决不了
  （与 9/6 报告策略 2"采分点强制专有绑定 + 通识可答即重生成的机器闸门"的判断一致）。
- 过程备注：claude_code 偶发 `unrecognized_model` 抖动一次，内置重试自愈，未影响结果。

## 6. 最终状态（截至 2026-09-07）

- **OE 定稿 = round3 集**：MiniMax-M3 **3.40** / glm-5.3-flash **3.85** / 整体 **3.62**
  （目标 2.5-3.5，差 0.12，在 ±0.2 测量噪声内）；20 题，自包含泄漏 0。
- 代码已提交（`574ccd5`）：错误并集强化、`--seed_solved`、每轮落盘、空输出重试。
- 继续下压的可行路径（按性价比）：① 机器闸门挡"通识可答"题；② 加第三 solver /
  换 gpt-5.6 标尺扩大错误并集；③ 换原创研究论文（专有事实密度根本更高）。

## 7. 产物（`molintbench/TCR-T-paper/`）

- 定稿：`oe_questions.json` / `.txt`（= round3，20 题，泄漏 0）
- 三轮测分：`oe_questions_solved_round{1,2}.json`（双 solver）、
  `oe_questions_solved_round3_{minimax,glm}.json`
- 快照：`oe_questions_round{2,3}.json`（round1 = 上一版定稿，备份于 `backup_loop3_20260906/`）
- 全程日志：`oe_scoring_20260906.log`
