# OE 难度闭环第二轮：续跑收尾报告

日期：2026-09-06
范围：`molintbench/TCR-T-paper` 9/5 新批次（新辩论 insights → MCQ/OE 各 20 题）的难度闭环收尾

---

## 0. 背景：9/5 中断在哪里

9/5 当天的完整时间线：

1. 03:05 新辩论产出 `insights.json`（10 条 insight）；
2. 04:35 MCQ 闭环跑完（定稿 = round3，即现行 `mcq_questions.json`）；
3. 07:38 OE 闭环第一轮跑完，定稿选了其 round2（当时即发现整体仍偏易）；
4. 07:39 将第一轮快照移入 `backup_loop2_v2/`，启动 OE 闭环第二轮继续压难度；
5. 第二轮：round2 快照 09:00（20 题全部重生成）、round3 快照 11:34（再改 12/20；
   **insight 7-10 的重生成返回空输出**，保留旧题）；
6. 进程在 round3 写盘后中断——round3 未测分、最终回写未执行。

另外：`difficulty_loop.py` 只把分数打到 stdout 不落盘，两轮闭环的全部分数丢失。
本次续跑用 `solve_and_grade.py`（结果落盘、可审计）补齐测量并完成定稿。

## 1. 本次补测（solver = MiniMax-M3，无原文盲做）

三份快照 × 两套 grader（luna = 文档口径；sol = `.env` 现行 GRADER_MODEL，更严），
solver 答案复用、仅换 grader 复判：

| 快照 | luna 均分 | sol 均分 | sol 分布 |
|---|---|---|---|
| canonical（第二轮起点） | 4.80 | 4.65 | 1分×1 3分×1 4分×1 5分×17 |
| round2 | **4.50** | **4.50** | 2分×1 3分×2 4分×3 5分×14 |
| round3 | 4.55 | 4.65 | 2分×1 4分×4 5分×15 |

MCQ 定稿（round3）补测：MiniMax-M3 **20/20（100%）**。

## 2. 决策与产物

- **round2 双口径均为最优**（round3 因 4 个 insight 重生成失败而回退），按闭环规则
  （最接近目标、平分取强化最深）回写为正式稿：`oe_questions.json` / `.txt`（20 题，
  自包含泄漏扫描 0）。原 canonical（第一轮定稿）已在 `backup_loop2_v2/` 留档，无损失。
- 全部落盘的审计文件（`molintbench/TCR-T-paper/`）：
  - `oe_solved_{canonical,round2,round3}_minimax.json`（luna 判分）
  - `oe_solved_{canonical,round2,round3}_minimax_sol.json`（sol 复判）
  - `mcq_solved_final_minimax.json`
  - 过程日志 `oe_scoring_20260906.log`

## 3. 结论

1. **难度平台期坐实**：4.80 → 4.50 → 4.55（±0.2 噪声内不再下降），距目标区间
   2.5–3.5 仍有约 1 分差距。两轮共 5 次强化后，措辞/结构层面的加固收益已耗尽。
2. **grader 严格度不是杠杆**：sol 与 luna 差 ≤0.15 分，且 round3 反而更高——
   是 solver 真会做，不是判分松。
3. 与 9/3 报告结论一致并再获验证：**瓶颈在内容**。TCR-T 是综述，其知识在强模型
   训练语料内；"不读原文就不知道"的区分度要靠原创研究论文的专有事实
   （具体数字、专有数据集/体系）。
4. **MCQ 天花板依旧**：新批次定稿仍 20/20。维持原建议：MCQ 定位"基础理解检查"，
   区分度主要押 OE + 严格 rubric。
5. **工程遗留**：`difficulty_loop.py` 重生成对网关空响应无重试（insight 7-10 因此
   未强化）；每轮分数不落盘。建议：regen 空输出时重试 1-2 次；loop 内复用
   `solve_and_grade` 的落盘式结果文件。

## 4. 下一步建议

- 若继续用 TCR-T 综述：接受 OE ≈4.5 的平台，或换"标尺 solver = gpt-5.6-luna/terra"
  再压（更强 solver 能暴露更多可强化点）；
- 更有性价比的路径：把闭环投向**原创研究论文**批次，验证专有事实类题目能否落进
  2.5–3.5 区间。
