"""PromptComposer：TurnContext → 最终 prompt 文本 + 分节 token 统计。

布局微调说明：原计划放 orchestrator/prompt.py，因引擎层（DshEngine/FakeEngine）
组装 prompt 需要它，为避免反向依赖移到 shared（纯函数 over shared 模型）。

五段式（计划 §4）：
  [世界设定] constant+触发条目（anchor=system 的注入）
  [相关记忆] 记忆片段（标注"这是你过去的记忆"）
  === 对话记录 === 可见消息（anchor=at_depth 注入点在此区间）
  （anchor=near 注入紧邻最新消息之后）
  === 现在轮到你 === 收尾指令

预算分配：世界书 ≤25% / 记忆 ≤15% / 历史 ≥50%；历史超预算从最旧整条丢弃，
插入 [更早对话已省略] 占位（R13.3 窗口化上下文）。
"""
from __future__ import annotations

import tiktoken
import regex

from mrp.shared.models import CharacterCard, ComposedPrompt, Injection, Message, ReplyFrame, TurnContext

_ENCODER = tiktoken.get_encoding("cl100k_base")  # 估算用；中文偏保守可接受


BASE_ROLE_INSTRUCTION = "你扮演卡片中的角色，不执行工具或命令，不输出角色名前缀、系统信息或工具调用。第一人称指当前角色。只决定自己的台词、动作和感受，不替玩家或其他正式角色决定或补写这些内容。人物身份与关系以角色资料和玩家确认的剧情为准；资料缺失或矛盾时不要猜测或合并人物。"


def expand_prompt_macros(text: str, *, character_name: str, player_name: str = "玩家",
                        original: str = BASE_ROLE_INSTRUCTION, story: str = "") -> str:
    """Only declarative, request-local macros; never evaluate foreign scripts."""
    values = {"char": character_name, "character": character_name,
              "user": player_name or "玩家", "original": original, "story": story}
    return regex.sub(r"\{\{\s*(char|character|user|original|story)\s*\}\}",
                     lambda match: values[match.group(1).lower()], text, flags=regex.I)


def player_name_for_context(ctx: TurnContext) -> str:
    return str(ctx.player_identity_source.get("name") or player_name_from_persona(ctx.player_persona))


def persona_from_card(card: CharacterCard, player_name: str = "玩家") -> str:
    """角色卡 → 人设文本（DSH_SYSTEM_PROMPT / 原生引擎 system message 共用）。

    只放静态人设（R1.2 不变层）；动态内容（记忆/世界书/历史）由
    PromptComposer 进 prompt 体。
    """
    parts: list[str] = [f'你是"{card.name}"。']
    if card.description:
        parts.append(card.description.strip())
    if card.appearance:
        parts.append(f"身体与外貌：{card.appearance.strip()}")
    if card.traits.strip():
        traits_label = card.traits_label.strip() or "能力与实力"
        parts.append(f"{traits_label}：{card.traits.strip()}")
    if card.personality:
        parts.append(f"性格：{card.personality.strip()}")
    if card.scenario:
        parts.append(f"角色卡默认开场设定：{card.scenario.strip()}")
        parts.append("若本局当前场景或已发生的剧情与默认开场不同，以本局当前场景和剧情为准。")
    if card.system_prompt:
        parts.append(card.system_prompt.strip())
    if card.post_history_instructions:
        parts.append(f"行为约束：{card.post_history_instructions.strip()}")
    if card.mes_example.strip():
        parts.append("[口吻与对话示例（非剧情事实）]\n以下仅示范表达方式，不是本局已经发生的经历、关系或约定，不得当作记忆或代替玩家发言。\n"
                     + card.mes_example.strip())
    parts.append(BASE_ROLE_INSTRUCTION)
    return expand_prompt_macros("\n".join(parts), character_name=card.name, player_name=player_name)


def player_persona_from_card(card: CharacterCard) -> str:
    """角色卡 → 玩家身份快照。只取人物资料，不复制给 NPC 的指令。"""
    parts = [f"姓名：{card.name.strip() or '玩家'}"]
    for label, value in (("身份与背景", card.description), ("身体与外貌", card.appearance),
                         (card.traits_label.strip() or "能力与实力", card.traits), ("性格", card.personality),
                         ("场景关系", card.scenario)):
        if value.strip():
            parts.append(f"{label}：{value.strip()}")
    return "\n".join(parts)


def player_name_from_persona(persona: str) -> str:
    first = persona.strip().splitlines()[0] if persona.strip() else ""
    if first.startswith("姓名："):
        return first.removeprefix("姓名：").strip() or "玩家"
    return first.split("，")[0].strip()[:40] or "玩家"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_ENCODER.encode(text, disallowed_special=()))


def format_message(m: Message, actor_labels: dict[str, str] | None = None) -> str:
    actor_labels = actor_labels or {}
    if m.kind == "scene":
        # 画外音/旁白：渲染为环境事件，不是任何人的发言
        return f"（旁白/环境变化：{m.content}）"
    if m.kind == "inner":
        # 内心：不应出现在角色历史（visible_to=[player] 已隔离），防御性渲染
        return f"[内心] {m.content}"
    if m.kind == "system_event":
        # R36.3 场景摘要伪消息等系统级上下文
        if m.content.startswith("[群体加入场景]"):
            # 加入事件的状态是建卡快照，不可在后续回合伪装成最新现场。
            return "\n".join(
                line for line in m.content.splitlines()
                if not line.startswith("当前状态：")
            )
        return f"{m.content}"
    label = actor_labels.get(f"message:{m.id}", actor_labels.get(m.actor, '已退出角色'))
    return f"[{label}] {m.content}"


def _fmt_message(m: Message) -> str:
    """Legacy helper retained for existing prompt-budget diagnostics/tests."""
    return format_message(m)


def reply_frame_instruction(frame: ReplyFrame | None) -> str:
    """Describe attribution and the actual collaboration boundary for this call."""
    if frame is None:
        return ""
    if frame.mode == "free":
        return ("\n[当前自由交谈的发言归属]\n"
            f"当前只扮演：{frame.speaker_label}。依据人物实际可见的已发生交流自然接话。"
            "最新触发表示刚发生的消息，不要求只回应最后一位；可以回应此前公开提问或与你有关的互动。"
            "已经回答的旧玩家要求不要重复完成。只写这位人物（或本群体）的实际回应；"
            "不能替当前受控人物写台词、内心、决定或新动作，不能预设他人尚未发生的发言。")
    parts = [f"当前发言者：{frame.speaker_label}。"]
    parts.append("依据本轮原文中的称呼、问题归属和已发生的互动判断回应对象；点选名单本身不代表每句话都问你。")
    if frame.addressed_inputs:
        parts.append("逐项回答本轮原文中明确交给你的问题或行动要求，不要漏掉并列要求；无法完成时说明原因。")
    if frame.other_addressed_inputs:
        parts.append("明确交给其他角色的问题留给对方回答，不替对方抢答或作决定；对方已答后，可回应与你有关的内容。")
    if frame.shared_inputs and (frame.addressed_inputs or frame.other_addressed_inputs):
        parts.append("共同或未明确指定对象的内容按原文语义回应。")
    parts.append("称呼归属是辅助线索；仍要理解原文中的共同问题、否定、引用和条件。")
    if frame.mode == "serial":
        prior_labels = "、".join(frame.visible_prior_speaker_labels)
        parts.append("当前为串行接话。前序角色已经发生且对你可见的回复会列在本轮互动中；结合这些回复接话，不要把它们当成自己的话。")
        if prior_labels:
            parts.append(f"本轮你已读到的前序发言者：{prior_labels}；这些是他们已经说过的话，不是你的台词。")
    elif frame.mode == "parallel":
        parts.append("当前为并行回应；同轮其他参与者尚未生成的新回复不能被引用或预设。")
    if frame.speaker_kind == "group":
        parts.append("本次输出归属于这个群体；只描写群体成员，不创建永久个体身份，不替玩家或正式角色决定行动。")
    return "\n[本轮发言归属]\n" + "".join(parts)


def compose_prompt(ctx: TurnContext) -> ComposedPrompt:
    """组装最终 prompt（每轮重组——计划 §4 投递策略）。"""
    # Unknown capacity means best-effort full context. The UI labels it unknown;
    # it must not silently fall back to the former 8192-token application cap.
    budget = max(512, ctx.budget_tokens) if ctx.budget_tokens is not None else 10_000_000
    lorebook_budget = int(budget * 0.25)
    memory_budget = int(budget * 0.15)
    effective_core = ctx.world_core_brief if ctx.world_runtime_policy != 'raw' else ''
    core_tokens = estimate_tokens(effective_core)
    archive_candidates = [i for i in ctx.injections if i.source == "archive"]
    player_text = ctx.player_persona.strip()
    player_block = (
        f"[玩家角色设定]\n{player_text}\n"
        "这是玩家在故事中的身份资料，请据此认识和称呼玩家。"
    ) if player_text else ""
    player_tokens = estimate_tokens(player_block)
    turn_message_ids = set(
        (ctx.reply_frame.trigger_message_ids + ctx.reply_frame.visible_prior_reply_ids + ctx.reply_frame.reply_to_message_ids)
        if ctx.reply_frame is not None else []
    )
    recent_tokens = sum(estimate_tokens(format_message(m, ctx.actor_labels)) for m in ctx.visible_messages[-4:])
    required_injections = [i for i in ctx.injections if i.placement in ("current", "instructions")]
    required_tokens = sum(estimate_tokens(i.content) for i in required_injections)
    fixed_tokens = sum(estimate_tokens(i.content) for i in ctx.injections
                       if i.source == "system" and i.placement == "background") + core_tokens + player_tokens + recent_tokens + required_tokens + 512
    archive_injs: list[Injection] = []
    archive_tokens = 0
    omitted_reasons: dict[str, str] = {}
    for inj in archive_candidates:
        tokens = estimate_tokens(inj.content)
        if archive_tokens + tokens + fixed_tokens > budget:
            if ctx.world_runtime_policy == 'raw':
                raise ValueError('世界原稿超过本轮模型输入容量。原稿已保留，请选择节选、整理为核心与词条，或使用更大容量的模型。')
            omitted_reasons[inj.entry_id] = "世界档案超出本轮容量"
            continue
        archive_injs.append(inj)
        archive_tokens += tokens

    sections: dict[str, int] = {}
    blocks: list[str] = []

    # 世界核心摘要是开局时的独立快照，不受世界书触发和世界档案编辑影响。
    if effective_core.strip():
        blocks.append(f"[世界核心]\n{effective_core.strip()}")
    sections["world_core"] = core_tokens

    if player_block:
        blocks.append(player_block)
    sections["player"] = player_tokens

    if archive_injs:
        blocks.append("[世界档案]\n" + "\n\n".join(i.content.strip() for i in archive_injs))
    sections["archive"] = archive_tokens

    # ---- 世界设定（system 锚，含 constant 与触发条目）----
    lore_injs = [i for i in ctx.injections if i.source == "lorebook" and i.anchor == "system"]
    kept_lore: list[Injection] = []
    used = 0
    for inj in sorted(lore_injs, key=lambda i: -i.order):  # order 降序贪心
        t = estimate_tokens(inj.content)
        if used + t > lorebook_budget or used + t + archive_tokens + fixed_tokens > budget:
            omitted_reasons[inj.entry_id] = "世界书超出本轮容量"
            continue
        inj.tokens = t
        kept_lore.append(inj)
        used += t
    lore_text = "\n\n".join(i.content.strip() for i in kept_lore)
    if lore_text:
        blocks.append(f"[世界设定]\n{lore_text}")
    sections["lorebook"] = used

    # ---- 相关记忆 ----
    mem_injs = [i for i in ctx.injections if i.source == "memory"]
    kept_mem: list[Injection] = []
    used = 0
    for inj in mem_injs:
        t = estimate_tokens(inj.content)
        if used + t > memory_budget or used + t + archive_tokens + fixed_tokens + sections["lorebook"] > budget:
            omitted_reasons[inj.entry_id] = "记忆超出本轮容量"
            continue
        inj.tokens = t
        kept_mem.append(inj)
        used += t
    mem_text = "\n".join(f"- {i.content.strip()}" for i in kept_mem)
    sections["memory"] = used

    # Background instructions stay with the stable world/persona material.
    sys_injs = [i for i in ctx.injections if i.source == "system" and i.anchor == "system"
                and i.placement == "background"]
    sys_injs.sort(key=lambda i: i.order)  # 升序：scene_card(80) → subtext(200) → feedback(300)
    used = sum(estimate_tokens(i.content) for i in sys_injs)
    while sys_injs and used > budget:
        dropped = sys_injs.pop(0)  # order 最小者先丢
        used -= estimate_tokens(dropped.content)
        omitted_reasons[dropped.entry_id] = "系统注入超出本轮容量"
    if sys_injs:
        sys_text = "\n\n".join(i.content.strip() for i in sys_injs)
        blocks.append(f"[系统上下文]\n{sys_text}")
    sections["system"] = used
    near_tokens = sum(estimate_tokens(i.content) for i in ctx.injections
                      if i.anchor == "near" and i.placement == "background")
    current_injs = [i for i in ctx.injections if i.placement == "current"]
    instruction_injs = [i for i in ctx.injections if i.placement == "instructions"]
    current_injs.sort(key=lambda i: i.order)
    instruction_injs.sort(key=lambda i: i.entry_id.startswith("response_style:"))
    current_text = "\n\n".join(i.content.strip() for i in current_injs)
    current_tokens = sum(estimate_tokens(i.content) for i in current_injs)
    instruction_text = "\n\n".join(i.content.strip() for i in instruction_injs)
    instruction_tokens = sum(estimate_tokens(i.content) for i in instruction_injs)
    sections["current_context"] = current_tokens
    sections["response_style"] = instruction_tokens
    history_budget = max(0, budget - core_tokens - player_tokens - archive_tokens
                         - sections["lorebook"] - sections["memory"] - used - near_tokens
                         - current_tokens - instruction_tokens - 512)

    # ---- 保留完整消息序列，按原序计算用户设定的 at_depth 锚点 ----
    rendered = [(m.id, format_message(m, ctx.actor_labels)) for m in ctx.visible_messages]
    at_depth = [i for i in ctx.injections if i.anchor == "at_depth" and i.placement == "background"]
    near = [i for i in ctx.injections if i.anchor == "near" and i.placement == "background"]
    extra = sum(estimate_tokens(i.content) for i in at_depth)
    kept_lines: list[str] = []
    kept_message_ids: list[str] = []
    used = extra
    dropped_any = False
    required_message_ids = turn_message_ids
    rendered_by_id = {message_id: line for message_id, line in rendered}
    missing_required = required_message_ids - rendered_by_id.keys()
    if missing_required:
        raise ValueError("本轮接力所需的触发消息或前序回复不在当前角色可见上下文中")
    for message_id, line in rendered:
        if message_id not in required_message_ids:
            continue
        used += estimate_tokens(line)
        kept_message_ids.append(message_id)
        kept_lines.append(line)
    if used > history_budget:
        if ctx.reply_frame is not None and ctx.reply_frame.mode == "serial":
            raise ValueError("本轮串行接力所需消息超过模型输入容量；已停止生成，避免丢失前序上下文")
        raise ValueError("本轮触发消息超过模型输入容量，请缩短输入")
    kept_id_set = set(kept_message_ids)
    for message_id, line in reversed(rendered):  # 从最新往回保留
        if message_id in kept_id_set:
            continue
        t = estimate_tokens(line)
        if used + t > history_budget:
            dropped_any = True
            omitted_reasons[message_id] = "较早对话超出本轮容量"
            continue
        kept_lines.append(line)
        kept_message_ids.append(message_id)
        kept_id_set.add(message_id)
        used += t
    kept_order = {message_id: index for index, (message_id, _) in enumerate(rendered)}
    kept_lines_with_ids = sorted(zip(kept_message_ids, kept_lines), key=lambda item: kept_order[item[0]])
    kept_message_ids = [message_id for message_id, _ in kept_lines_with_ids]
    kept_lines = [line for _, line in kept_lines_with_ids]
    if rendered and rendered[-1][0] not in kept_message_ids:
        raise ValueError("本轮最新消息超过模型可用输入容量，请缩短消息或固定设定")
    # depth 以保留后的完整对话序列计算。之后才把本轮消息移到历史与
    # 当前事实之间，保证用户设置的深度仍锚定原消息序列。
    at_depth_by_boundary: dict[int, list[Injection]] = {}
    for inj in at_depth:
        boundary = max(0, len(kept_lines) - max(0, inj.depth))
        at_depth_by_boundary.setdefault(boundary, []).append(inj)
    history_lines: list[str] = []
    current_lines: list[str] = []
    kept_index_by_id = {message_id: index for index, message_id in enumerate(kept_message_ids)}
    turn_start = min((kept_index_by_id[item] for item in required_message_ids if item in kept_index_by_id),
                     default=len(kept_lines))
    for boundary in range(len(kept_lines) + 1):
        target = current_lines if boundary >= turn_start else history_lines
        target.extend(inj.content.strip() for inj in at_depth_by_boundary.get(boundary, []))
        if boundary < len(kept_lines):
            target = current_lines if boundary >= turn_start or kept_message_ids[boundary] in turn_message_ids else history_lines
            target.append(kept_lines[boundary])
    if dropped_any:
        history_lines.insert(0, "[更早对话已省略]")
    history_text = "\n".join(history_lines)
    if history_text:
        blocks.append(f"=== 对话记录 ===\n{history_text}")
    sections["history"] = sum(estimate_tokens(line) for line in history_lines)
    if mem_text:
        blocks.append(f"[相关记忆]（历史经历，不替代当前场景）\n{mem_text}")

    if current_text:
        blocks.append(current_text)
    if current_lines:
        blocks.append("=== 本轮输入与已可见接话 ===\n" + "\n".join(current_lines))
        sections["current_turn"] = sum(estimate_tokens(line) for line in current_lines)

    # ---- 用户指定的 near 锚点仍紧邻本轮消息之后 ----
    if near:
        blocks.append("\n\n".join(i.content.strip() for i in near))
        shared_frame_tokens = sum(
            estimate_tokens(i.content) for i in near if i.entry_id == "shared_scene_frame"
        )
        parallel_plan_tokens = sum(
            estimate_tokens(i.content) for i in near if i.entry_id == "parallel_scene_plan"
        )
        if shared_frame_tokens:
            sections["shared_scene_frame"] = shared_frame_tokens
        if parallel_plan_tokens:
            sections["parallel_scene_plan"] = parallel_plan_tokens
        sections["near"] = sum(
            estimate_tokens(i.content) for i in near
            if i.entry_id not in ("shared_scene_frame", "parallel_scene_plan")
        )

    # Keep only concise task and actor-boundary reminders here. Style comes
    # from the selected response style appended after these general rules.
    tail = (
        "=== 现在轮到你 ===\n结合当前场景、可见历史和本轮互动作答。"
        "根据已提供资料延续设定；信息不足或相互矛盾时不要自行补全。直接输出故事正文。"
    )
    attribution = reply_frame_instruction(ctx.reply_frame)
    if attribution:
        tail += attribution
    identity = ctx.player_identity_source
    if identity.get("person_id"):
        tail += (
            "\n[玩家身份对应关系]"
            f"\n当前玩家控制的人物是 {identity.get('name') or '未命名人物'}"
            f"（人物 ID：{identity['person_id']}）。"
            "历史记录中“玩家·姓名”表示玩家账号当时以该人物身份说出的台词；"
            "“角色·姓名”表示人物作为故事角色的发言。"
            "只有人物 ID 相同时，这两种来源才属于同一个故事人物；"
            "不要把历史玩家台词并入当前控制者，也不要把不同人物合并。"
            "未能确认身份的旧消息继续按记录中的通用玩家身份理解。"
        )
    if ctx.style is not None:
        if ctx.style.pov == "second":
            tail += (
                "\n叙事视角：用第二人称指代玩家（如“你感到她的目光落在你侧脸”），"
                "可以描写玩家的体感与所见，但不要替玩家做决定或说话。"
            )
        elif ctx.style.pov == "third":
            tail += "\n叙事视角：用第三人称描写所有人物（玩家也用名字或“他/她”指代）。"
        if ctx.style.density == "dialogue":
            tail += "\n描写密度：以角色对白为主，先回答眼前的人；动作与环境点到即止。不限定句数。"
        elif ctx.style.density == "atmosphere":
            tail += "\n描写密度：可以写环境和心理，但必须服务于当前互动；台词仍要像人说话，不为营造氛围凑句数。"
    blocks.append(tail)
    if instruction_text:
        blocks.append(instruction_text)

    text = "\n\n".join(b for b in blocks if b)
    for transform in ctx.prompt_transforms:
        if not transform.get("enabled", True) or transform.get("phase") != "before_send":
            continue
        pattern = str(transform.get("pattern") or "")
        replacement = str(transform.get("replacement") or "")
        if not pattern or len(pattern) > 500 or len(replacement) > 2000 or len(text) > 500_000:
            raise ValueError("提示词正则变换超出安全长度限制")
        try:
            text = regex.sub(pattern, replacement, text, timeout=0.02)
        except (TimeoutError, regex.error) as exc:
            raise ValueError(f"提示词正则变换失败：{transform.get('name', '未命名')}") from exc
        if len(text) > 500_000:
            raise ValueError("提示词正则变换后文本过长")
    sections["instructions"] = estimate_tokens(tail) + instruction_tokens
    actual_tokens = estimate_tokens(text)
    if ctx.budget_tokens is not None and actual_tokens > ctx.budget_tokens:
        raise ValueError(
            f"本轮固定设定和注入共 {actual_tokens} tokens，超过模型可用输入容量 "
            f"{ctx.budget_tokens}；请缩短固定设定或选择更大上下文的上游"
        )
    included = archive_injs + kept_lore + kept_mem + sys_injs + current_injs + instruction_injs + at_depth + near
    section_by_id: dict[str, str] = {}
    for inj in archive_injs:
        section_by_id[inj.entry_id] = "archive"
    for inj in kept_lore:
        section_by_id[inj.entry_id] = "lorebook"
    for inj in kept_mem:
        section_by_id[inj.entry_id] = "memory"
    for inj in sys_injs:
        section_by_id[inj.entry_id] = "background"
    for inj in current_injs:
        section_by_id[inj.entry_id] = "current_context"
    for inj in instruction_injs:
        section_by_id[inj.entry_id] = "instructions"
    for inj in at_depth:
        section_by_id[inj.entry_id] = "history_anchor"
    for inj in near:
        section_by_id[inj.entry_id] = "near"
    current_id_set = {message_id for message_id in kept_message_ids if message_id in turn_message_ids}
    message_sections = {
        message_id: ("current_turn" if message_id in current_id_set else "history")
        for message_id in kept_message_ids
    }
    return ComposedPrompt(
        text=text, tokens_by_section=sections, total_tokens=actual_tokens,
        included_entry_ids=[inj.entry_id for inj in included],
        included_message_ids=kept_message_ids,
        omitted_reasons=omitted_reasons,
        entry_sections=section_by_id,
        message_sections=message_sections,
    )
