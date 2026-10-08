#!/usr/bin/env bash
# UI 冒烟测试：fake 引擎服务器 + playwright 浏览器自动化走完整用户路径。
# 零 LLM 成本（FakeEngine 秒回；候选生成由 FakeAssistGenerator 确定性返回，不点不生成）。
# 用法: tools/linux/ui_smoke.sh   （需 playwright-cli 已装、~/.cache/ms-playwright 有浏览器）
set -u
P="$(cd "$(dirname "$0")/../.." && pwd)"
PORT=8017
BASE="http://127.0.0.1:$PORT"
LOG=/tmp/mrp-smoke-server.log
PASS=0; FAIL=0

say()  { echo "[smoke] $*"; }
ok()   { PASS=$((PASS+1)); echo "  [PASS] $*"; }
bad()  { FAIL=$((FAIL+1)); echo "  [FAIL] $*"; }

# 断言：页面文本包含指定内容
assert_text() { # $1=期望包含文本 $2=步骤名
  local out
  out=$(playwright-cli eval "document.body.innerText.includes($(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$1"))" 2>/dev/null | grep -A1 '### Result' | tail -1)
  if [[ "$out" == *"true"* ]]; then ok "$2"; else bad "$2（页面缺少: $1）"; fi
}
assert_text_not() {
  local out
  out=$(playwright-cli eval "document.body.innerText.includes($(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$1"))" 2>/dev/null | grep -A1 '### Result' | tail -1)
  if [[ "$out" == *"false"* ]]; then ok "$2"; else bad "$2（不应出现: $1）"; fi
}

say "1. 启动 fake 模式服务器（端口 $PORT）"
fuser -k ${PORT}/tcp 2>/dev/null; sleep 1
FAKE_REPLY="这是一段用于验证流式输出节奏的较长回复文本，逐字释放应该在两次采样之间表现为长度递增，最终完整呈现。"
FAKE_PAD="灯光在幕布上投下摇晃的影子"
(cd "$P" && MRP_FAKE_ENGINE=1 MRP_FAKE_REPLY="$FAKE_REPLY" MRP_FAKE_JUDGE=flag MRP_FAKE_DIRECTOR=switch MRP_FAKE_PADDING="$FAKE_PAD" MRP_DATA_ROOT=/tmp/mrp-smoke-data setsid nohup "$P/.venv/bin/python" \
  -m uvicorn mrp.server.app:app --host 127.0.0.1 --port $PORT --app-dir "$P/src" \
  > "$LOG" 2>&1 < /dev/null &)
for i in $(seq 1 30); do sleep 6; curl -s -m 5 "$BASE/api/v1/health" >/dev/null 2>&1 && break; done  # cephfs 冷启动可达 2-3 分钟
curl -s -m 3 "$BASE/api/v1/health" | grep -q '"ok":true' && ok "服务器就绪" || { bad "服务器未就绪"; tail -5 "$LOG"; exit 1; }

# 种测试数据：两角色 + 一会话
CID_A=$(curl -s -X POST "$BASE/api/v1/characters/import" -F 'file={"name":"冒烟甲","description":"测试角色A","first_mes":"A的开场白"};filename=a.json;type=application/json' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
CID_B=$(curl -s -X POST "$BASE/api/v1/characters/import" -F 'file={"name":"冒烟乙","description":"测试角色B","first_mes":"B的开场白"};filename=b.json;type=application/json' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
SID=$(curl -s -X POST "$BASE/api/v1/sessions" -H 'content-type: application/json' \
  -d "{\"title\":\"冒烟会话\",\"character_ids\":[\"$CID_A\",\"$CID_B\"]}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["meta"]["id"])')
[[ -n "$SID" && "$SID" != *Error* ]] && ok "测试数据就绪（会话 $SID）" || bad "测试数据创建失败"

say "2. 浏览器打开与 bootstrap"
playwright-cli open "$BASE" >/dev/null 2>&1
for i in $(seq 1 10); do sleep 2; playwright-cli eval "document.body.innerText.includes('MRP')" 2>/dev/null | grep -q true && break; done
assert_text "多角色扮演框架" "页面加载"
# 清持久化（辅助栏开合等 localStorage）→ 保证"默认态"断言每次成立；然后重新 bootstrap
# 注：playwright-cli eval 只接受单个表达式，多语句必须包成 IIFE
playwright-cli eval "(()=>{localStorage.clear(); location.reload(); return 'reloading';})()" >/dev/null 2>&1
for i in $(seq 1 10); do sleep 2; playwright-cli eval "document.body.innerText.includes('多角色扮演框架')" 2>/dev/null | grep -q true && break; done
sleep 6  # bootstrap（角色/世界书/会话加载 + SSE）
assert_text "已连接" "SSE 连接"
assert_text "冒烟会话" "会话列表加载"
assert_text "A的开场白" "开场白渲染（open_round 结果）"

say "2.5 辅助栏（M12-R41：默认收起 → 点击生成候选 → 预填 → ✕ 关闭）"
# 默认收起：面板不在 DOM
PANEL_N=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-panel\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$PANEL_N" == "0" ]] && ok "辅助栏默认收起（不打扰）" || bad "辅助栏默认应收起: $PANEL_N"
# 打开面板（头部「辅助」按钮）
playwright-cli eval "document.querySelector('[data-testid=\"assist-toggle\"]')?.click()" >/dev/null 2>&1
sleep 1
assert_text "导演与场景" "辅助栏：导演与场景区"
assert_text "叙事视角" "辅助栏：叙事区"
# 零自动：开场 first_mes 已落账，但未点击 → 不应有候选
CAND0=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$CAND0" == "0" ]] && ok "候选零自动（未点击时无候选）" || bad "候选不应自动出现: $CAND0"
# 点击生成（冷启动点 → 开场候选，fake 确定性 3 条）
playwright-cli eval "document.querySelector('[data-testid=\"assist-generate\"]')?.click()" >/dev/null 2>&1
for i in $(seq 1 10); do sleep 2; playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]').length" 2>/dev/null | grep -q '"3"' && break; done
N_CAND=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$N_CAND" == "3" ]] && ok "生成候选：3 条" || bad "候选条数异常: $N_CAND"
assert_text "开场候选" "来源标签（开场候选）"
assert_text "@冒烟甲" "候选定向在场角色"
# 点击第一条 → 清空后覆盖填入（不发送，D14）+ 定向替换为 @对象
playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]')[0]?.click()" >/dev/null 2>&1
sleep 1
BOX=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BOX" == *"接一句"* ]] && ok "点击候选填入输入框（不发送，D14）" || bad "预填未生效: $BOX"
SEL_A=$(playwright-cli eval "Array.from(document.querySelectorAll('button')).some(b => b.className.includes('rounded-full') && b.textContent.includes('冒烟甲') && b.className.includes('bg-indigo-50'))" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$SEL_A" == *"true"* ]] && ok "预填替换定向为 @对象" || bad "定向未跟随候选"
# 点第二条 → 覆盖（不叠加：上一条消失）
playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]')[1]?.click()" >/dev/null 2>&1
sleep 1
BOX2=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
if [[ "$BOX2" == *"四处看看"* && "$BOX2" != *"接一句"* ]]; then ok "更换候选=覆盖（不叠加，2026-09-25 修订）"; else bad "覆盖失败: $BOX2"; fi
# 同一条再点一次 → 不重复（幂等）
playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]')[1]?.click()" >/dev/null 2>&1
sleep 1
CNT=$(playwright-cli eval "document.querySelector('textarea').value.split('四处看看').length - 1" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$CNT" == "1" ]] && ok "重复点同一条不叠加（幂等）" || bad "重复点击叠加了 $CNT 次"
# 玩家自己的草稿被覆盖 → 出现恢复入口 → 一键恢复
playwright-cli fill "textarea" "我自己的草稿" >/dev/null 2>&1
playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]')[2]?.click()" >/dev/null 2>&1
sleep 1
BOX3=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BOX3" == *"先别急"* ]] && ok "点候选覆盖玩家草稿（清空后覆盖）" || bad "草稿未被覆盖: $BOX3"
playwright-cli eval "document.querySelector('[data-testid=\"composer-restore-draft\"]')?.click()" >/dev/null 2>&1
sleep 1
BOX4=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BOX4" == *"我自己的草稿"* ]] && ok "覆盖后一键恢复草稿（后悔药）" || bad "恢复草稿失败: $BOX4"
# 换一批不触碰输入框（此时框里是恢复后的草稿）
BTN=$(playwright-cli eval "document.querySelector('[data-testid=\"assist-generate\"]')?.textContent" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BTN" == *"换一批"* ]] && ok "有批次时按钮=换一批" || bad "按钮文案异常: $BTN"
playwright-cli eval "document.querySelector('[data-testid=\"assist-generate\"]')?.click()" >/dev/null 2>&1
sleep 4
BOX5=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BOX5" == *"我自己的草稿"* ]] && ok "换一批不改动玩家草稿" || bad "换一批覆盖了输入框: $BOX5"
# ✕ 关闭本批 → 候选清空 + 按钮回到「生成候选」
playwright-cli eval "document.querySelector('[data-testid=\"assist-dismiss\"]')?.click()" >/dev/null 2>&1
sleep 1
CAND_AFTER=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$CAND_AFTER" == "0" ]] && ok "✕ 关闭本批（候选清空）" || bad "关闭本批失败: $CAND_AFTER"
# 再生成 → 收起面板 → 角标亮（有候选待用）
playwright-cli eval "document.querySelector('[data-testid=\"assist-generate\"]')?.click()" >/dev/null 2>&1
sleep 4
playwright-cli eval "document.querySelector('[data-testid=\"assist-toggle\"]')?.click()" >/dev/null 2>&1
sleep 1
BADGE=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-badge\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$BADGE" == "1" ]] && ok "收起后角标提示有候选待用" || bad "角标未亮: $BADGE"
playwright-cli eval "document.querySelector('[data-testid=\"assist-toggle\"]')?.click()" >/dev/null 2>&1   # 重新打开，供后续段落使用
sleep 1
# 复位：取消定向选中（避免影响 section 3 的"点击选中"断言）
playwright-cli eval "(() => { const c = Array.from(document.querySelectorAll('button')).filter(b => b.className.includes('rounded-full') && b.textContent.includes('冒烟甲') && b.className.includes('bg-indigo-50')).pop(); if (!c) return 'not-selected'; c.click(); return 'unselected'; })()" >/dev/null 2>&1
sleep 1

say "3. 收件人栏与定向"
# 点"冒烟甲"头像 chip → 选中态
playwright-cli eval "(() => { const c = Array.from(document.querySelectorAll('button')).find(b => b.className.includes('rounded-full') && b.textContent.includes('冒烟甲')); if (!c) return 'no-chip'; c.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "点击收件人 chip" || bad "收件人 chip 未找到或点击失败"
sleep 1
SEL=$(playwright-cli eval "Array.from(document.querySelectorAll('button')).some(b => b.className.includes('bg-indigo-50') && b.textContent.includes('冒烟甲'))" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$SEL" == *"true"* ]] && ok "定向选中态（indigo 高亮）" || bad "选中态未出现"

say "4. 发送定向消息（FakeEngine）"
# 4.4 R33 流式探针：发送前在浏览器内安装 80ms 间隔采样器（bash 侧轮询往返
# 太慢会错过打字窗口），观测最后气泡文本长度的增长/部分态
TOTAL=${#FAKE_REPLY}
playwright-cli eval "(() => {
  window.__streamProbe = new Promise((resolve) => {
    let last = -2, growth = false, partial = false, t0 = Date.now();
    const timer = setInterval(() => {
      const els = document.querySelectorAll('.whitespace-pre-wrap');
      const el = els.length ? els[els.length - 1] : null;
      // 打字光标 ▍ 属于 pending 态装饰，不计入正文长度（否则 +1 会提前破坏完整性判定）
      const raw = el ? el.textContent : '';
      const len = el ? raw.replace(/▍/g, '').length : -1;
      if (last >= 0 && len > last) growth = true;
      if (last > 0 && last < $TOTAL) partial = true;
      last = len;
      const settled = len >= $TOTAL && !raw.includes('▍');
      if (settled || Date.now() - t0 > 15000) {
        clearInterval(timer);
        resolve(JSON.stringify({growth, partial, finalLen: len}));
      }
    }, 80);
  });
  return 'installed';
})()" 2>/dev/null | grep -A1 '### Result' | grep -q installed && ok "流式探针安装" || bad "流式探针安装失败"

playwright-cli fill "textarea" "冒烟甲你好，定向测试" >/dev/null 2>&1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '发送'); b.click(); return 'sent'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q sent && ok "消息发送" || bad "发送失败"

PROBE=$(playwright-cli eval "window.__streamProbe" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '\\')
if echo "$PROBE" | grep -q '"growth":true'; then ok "流式逐字释放（长度递增可见，R33）"; else bad "未捕捉到流式节奏: $PROBE"; fi
if echo "$PROBE" | grep -q "\"finalLen\":$TOTAL"; then ok "流式最终完整（$TOTAL/$TOTAL 字）"; else bad "回复不完整: $PROBE"; fi
assert_text "逐字释放" "FakeEngine 回复渲染"
# R34：flag judge 两查均违规 → 放行 + ⚠️ 角标
assert_text "校验未过" "输出卫生角标（R34 flag 模式）"
# 决策横幅：explicit_mention 且只有冒烟甲
BANNER=$(playwright-cli eval "document.body.innerText" 2>/dev/null | grep -A1 '### Result' | tail -1)
if echo "$BANNER" | grep -q "冒烟甲 被选中" ; then ok "导演决策显示定向（辅助栏）"; else bad "导演决策未显示定向"; fi

say "4.5 消息操控（R32/R45：图标按钮重roll + 候选切换）"
# R45 图标化：最后一条角色气泡悬停出现图标行——点刷新图标（title 唯一标识 swipe）
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).filter(x => (x.title||'').includes('重新生成（保留候选历史')).pop(); if (!b) return 'no-swipe-btn'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "点击刷新图标重roll" || bad "刷新图标未找到"
sleep 4
assert_text "2/2" "重roll 后候选计数（2/2）"
# 切回上一个候选 → 1/2
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button[title=\"上一个候选\"]')).pop(); if (!b) return 'no-prev'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "切换到上一候选" || bad "候选切换按钮未找到"
sleep 2
assert_text "1/2" "候选切换生效（1/2）"

say "4.6 玩家消息重新生成（R45：基于改后文本重跑最后一轮）"
BEFORE_N=$(playwright-cli eval "document.querySelectorAll('.whitespace-pre-wrap').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).filter(x => (x.title||'').includes('重跑这一轮')).pop(); if (!b) return 'no-regen-btn'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "点击玩家消息刷新图标" || bad "玩家消息刷新图标未找到"
AFTER_N=""
for i in $(seq 1 20); do sleep 1; AFTER_N=$(playwright-cli eval "document.querySelectorAll('.whitespace-pre-wrap').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"'); [[ "$AFTER_N" == "$BEFORE_N" ]] && break; done
[[ -n "$AFTER_N" && "$AFTER_N" == "$BEFORE_N" ]] && ok "重跑完成（气泡数 $BEFORE_N→$AFTER_N，旧回复已替换）" || bad "重跑后气泡数异常（$BEFORE_N→$AFTER_N）"
assert_text_not "操作失败" "重跑无失败提示"

say "5. 候选（对话接话：手动生成）"
# 已发过消息（非冷启动点）→ 生成的是"接话候选"；辅助栏在 2.5 段保持打开
for i in $(seq 1 10); do
  PANEL_N=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-panel\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
  [[ "$PANEL_N" == "1" ]] && break
  playwright-cli eval "document.querySelector('[data-testid=\"assist-toggle\"]')?.click()" >/dev/null 2>&1
  sleep 1
done
playwright-cli eval "document.querySelector('[data-testid=\"assist-generate\"]')?.click()" >/dev/null 2>&1
for i in $(seq 1 10); do sleep 2; playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]').length" 2>/dev/null | grep -q '"3"' && break; done
assert_text "接话候选" "来源标签（接话候选）"
# M12-R41 D14：候选点击"填入"（不直发）
playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]')[0]?.click()" >/dev/null 2>&1
sleep 1
BOX5=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BOX5" == *"接一句"* ]] && ok "回合候选点击填入（D14 全局预填）" || bad "回合候选预填未生效: $BOX5"
playwright-cli fill "textarea" "" >/dev/null 2>&1

say "6. 工坊 Tab"
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '工坊'); t.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 2
assert_text "角色工坊" "工坊 Tab 渲染"
assert_text "世界书工坊" "世界书子 Tab 渲染"
assert_text "生成" "生成表单渲染"

say "7. 辅助栏 v2：检查器 / 存档·用量（从全局 Tab 迁入）+ console 错误"
# 回到聊天页 + 确保面板打开、在「辅助」标签
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '聊天'); if (t) t.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 1
for i in $(seq 1 6); do
  PANEL_N=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-panel\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
  [[ "$PANEL_N" == "1" ]] && break
  playwright-cli eval "document.querySelector('[data-testid=\"assist-toggle\"]')?.click()" >/dev/null 2>&1
  sleep 1
done
# 全局导航已瘦身：注入检查器/会话与存档 两个 Tab 不应存在
TAB_N=$(playwright-cli eval "Array.from(document.querySelectorAll('button')).filter(b => ['注入检查器','会话与存档'].includes(b.textContent.trim())).length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$TAB_N" == "0" ]] && ok "全局导航已移除检查器/存档 Tab（7→5）" || bad "全局仍有 $TAB_N 个旧 Tab"
# 面板内部标签页存在
assert_text "存档·用量" "面板标签页渲染"
# 宽窄切换
playwright-cli eval "document.querySelector('[data-testid=\"assist-width\"]')?.click()" >/dev/null 2>&1
sleep 1
WTXT=$(playwright-cli eval "document.querySelector('[data-testid=\"assist-width\"]')?.textContent" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$WTXT" == *"收窄"* ]] && ok "面板加宽/收窄切换" || bad "宽窄切换失败: $WTXT"
# 检查器：辅助标签的「查看本轮上下文 ↗」→ 自动切到「检查」标签并查询
playwright-cli eval "document.querySelector('[data-testid=\"assist-tab-assist\"]')?.click()" >/dev/null 2>&1
sleep 1
playwright-cli eval "document.querySelector('[data-testid=\"inspect-this-turn\"]')?.click()" >/dev/null 2>&1
for i in $(seq 1 8); do sleep 1; playwright-cli eval "document.body.innerText.includes('合计 ≈')" 2>/dev/null | grep -q true && break; done
INSPECT_TAB=$(playwright-cli eval "document.querySelector('[data-testid=\"assist-tab-inspect\"]')?.className.includes('text-indigo-600')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$INSPECT_TAB" == *true* ]] && ok "导演区联动：自动切到「检查」标签" || bad "未切换到检查标签"
assert_text "对话记录" "检查器渲染 prompt 分节"
assert_text "合计 ≈" "检查器 token 统计"
# 崩溃防线（2026-09-25 事故：未知分节 kind 渲染 undefined → 整页白屏）：查询后页面必须还活着
ALIVE=$(playwright-cli eval "!!document.querySelector('textarea')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$ALIVE" == *true* ]] && ok "检查器查询后页面未崩溃" || bad "检查器查询导致页面崩溃"
# 存档·用量：存档创建 + 用量（辅助候选成本分列）
playwright-cli eval "document.querySelector('[data-testid=\"assist-tab-saves\"]')?.click()" >/dev/null 2>&1
sleep 1
assert_text "用量（token）" "用量区渲染"
assert_text "辅助候选" "assist 成本分列可见（候选点击已计账）"
playwright-cli fill "[data-testid=\"assist-saves\"] input" "冒烟存档" >/dev/null 2>&1
playwright-cli eval "document.querySelector('[data-testid=\"save-create\"]')?.click()" >/dev/null 2>&1
sleep 2
assert_text "冒烟存档" "创建存档并出现在列表"
# 回到「辅助」标签（后续段落依赖）
playwright-cli eval "document.querySelector('[data-testid=\"assist-tab-assist\"]')?.click()" >/dev/null 2>&1
sleep 1
CONSOLE_ERRS=$(playwright-cli console error 2>/dev/null | grep -cv "^#" || true)
if [[ "$CONSOLE_ERRS" -le 2 ]]; then ok "console 无严重错误（$CONSOLE_ERRS 条）"; else bad "console 错误 $CONSOLE_ERRS 条"; playwright-cli console error 2>/dev/null | tail -5; fi

say "8. 场景演化（S5：LLM 导演切场景）"
# 回到聊天页 + 发一条无提及消息 → FakeDirectorJudge=switch 切场景
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '聊天'); if (t) t.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 1
CID_B=$(curl -s "$BASE/api/v1/sessions" | python3 -c "import json,sys; ss=json.load(sys.stdin); s=[x for x in ss if x['title']=='冒烟会话'][0]; print(s['character_ids'][1])")
playwright-cli fill "textarea" "走吧，去天台" >/dev/null 2>&1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '发送'); b.click(); return 'sent'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q sent && ok "S5 旁白消息发送" || bad "S5 发送失败"
sleep 3
assert_text "场景转换" "S5 过渡消息渲染（场景转换条）"
# M12-R41 右栏化：场景切换信息在「导演与场景」区（activeScene 跟随 scene.switched）
assert_text "当前场景 · 天台" "S5 辅助栏当前场景跟随切换"
# 离场者：乙不在新场景（members=第一个在场者=甲）
SCENES=$(curl -s "$BASE/api/v1/sessions/$(curl -s $BASE/api/v1/sessions | python3 -c "import json,sys; ss=json.load(sys.stdin); print([x['id'] for x in ss if x['title']=='冒烟会话'][0])")/scenes")
echo "$SCENES" | python3 -c "
import json,sys
d = json.load(sys.stdin)
new = [s for s in d['scenes'] if s['id'] == d['active_scene_id']][0]
assert new['title'] == '天台', new['title']
assert '$CID_B' not in new['member_ids'], new['member_ids']
print('scene-ok')" | grep -q scene-ok && ok "S5 新场景在场名单（乙离场）" || bad "S5 场景名单异常"

say "9. 记忆（S6：固化→查看→删除）"
SID=$(curl -s $BASE/api/v1/sessions | python3 -c "import json,sys; ss=json.load(sys.stdin); print([x['id'] for x in ss if x['title']=='冒烟会话'][0])")
CID_A2=$(curl -s "$BASE/api/v1/sessions/$SID" | python3 -c "import json,sys; print(json.load(sys.stdin)['meta']['character_ids'][0])")
# 记录来源：S5 场景切换的 bg 边界固化（或这里的手动补充——以存在性为准，不问来源）
curl -s -X POST "$BASE/api/v1/sessions/$SID/memory/consolidate" -o /dev/null
curl -s "$BASE/api/v1/characters/$CID_A2/memories?session_id=$SID" | python3 -c "
import json,sys
r = json.load(sys.stdin)
assert len(r['records']) >= 1, r
print('has-records')" | grep -q has-records && ok "S6 固化产出记录（bg 边界+手动）" || bad "S6 无记忆记录"
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '记忆'); b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "S6 记忆查看器打开" || bad "S6 查看器打开失败"
sleep 2
assert_text "情景" "S6 记忆记录可见（kind 分组）"
MEM_BEFORE=$(curl -s "$BASE/api/v1/characters/$CID_A2/memories?session_id=$SID" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['records']))")
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '删除'); if (!b) return 'no-del'; b.click(); return 'clicked'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q clicked && ok "S6 点击删除" || bad "S6 删除按钮未找到"
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '删除' && x.className.includes('bg-rose-500')); if (!b) return 'no-confirm'; b.click(); return 'deleted'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q deleted && ok "S6 确认删除" || bad "S6 删除确认失败"
sleep 1
MEM_AFTER=$(curl -s "$BASE/api/v1/characters/$CID_A2/memories?session_id=$SID" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['records']))")
if [[ "$MEM_AFTER" -lt "$MEM_BEFORE" ]]; then ok "S6 删除生效（$MEM_BEFORE→$MEM_AFTER，角色忘记）"; else bad "S6 删除未生效（$MEM_BEFORE→$MEM_AFTER）"; fi
playwright-cli eval "(() => { document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape'})); const overlay = document.querySelector('.fixed.inset-0.z-40'); if (overlay) overlay.dispatchEvent(new MouseEvent('click', {bubbles:true})); return 'closed'; })()" >/dev/null 2>&1

say "10. 叙事控制（S7：视角/密度/垫场；M12-R41 右栏化）"
# 叙事控制已从头部弹层搬入辅助栏——确保面板打开后直接操作
for i in $(seq 1 6); do
  PANEL_N=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-panel\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
  [[ "$PANEL_N" == "1" ]] && break
  playwright-cli eval "document.querySelector('[data-testid=\"assist-toggle\"]')?.click()" >/dev/null 2>&1
  sleep 1
done
playwright-cli eval "document.querySelector('[data-testid=\"assist-tab-assist\"]')?.click()" >/dev/null 2>&1  # 确保在「辅助」标签
sleep 1
assert_text "叙事视角" "S7 辅助栏叙事区（右栏化）"
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '第二人称'); if (!b) return 'no-btn'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "S7 切第二人称" || bad "S7 第二人称按钮未找到"
sleep 1
# 性能回归（2026-09-25）：叙事切换=乐观高亮+PATCH 回填，**不得**触发整表刷新（GET /sessions 慢）
playwright-cli eval "(()=>{performance.clearResourceTimings(); return 'cleared';})()" >/dev/null 2>&1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '第三人称'); if (!b) return 'no-btn'; b.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 2
LIST_REQ=$(playwright-cli eval "performance.getEntriesByType('resource').filter(e => e.name.endsWith('/api/v1/sessions')).length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$LIST_REQ" == "0" ]] && ok "S7 叙事切换不触发整表刷新（性能回归）" || bad "S7 叙事切换触发了 $LIST_REQ 次列表请求"
HL=$(playwright-cli eval "Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '第三人称')?.className.includes('bg-indigo-50')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$HL" == *true* ]] && ok "S7 切换后选中态跟随" || bad "S7 第三人称选中态未出现"
# 还原为第二人称（后续 inspection 断言依赖）
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '第二人称'); if (!b) return 'no-btn'; b.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '对话流'); if (!b) return 'no-btn'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "S7 切对话流密度" || bad "S7 对话流按钮未找到"
sleep 1
# 先点收件人 chip（@定向 → 快路径，保证角色生成 → inspection 存在；
# 无提及时 FakeDirectorJudge=switch 会切场景而不选角）
playwright-cli eval "(() => { const c = Array.from(document.querySelectorAll('button')).find(b => b.className.includes('rounded-full') && b.textContent.includes('冒烟甲')); if (!c) return 'no-chip'; c.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "S7 选择收件人" || bad "S7 收件人 chip 未找到"
# 发消息 → inspection 断言 prompt 含指令（fake 回复固定，长度断言不可行）
playwright-cli fill "textarea" "随便聊聊今天的事" >/dev/null 2>&1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '发送'); b.click(); return 'sent'; })()" >/dev/null 2>&1
sleep 3
CID_A=$(curl -s "$BASE/api/v1/sessions" | python3 -c "import json,sys; ss=json.load(sys.stdin); s=[x for x in ss if x['title']=='冒烟会话'][0]; print(s['character_ids'][0])")
LAST_TURN=$(curl -s "$BASE/api/v1/sessions/$SID" | python3 -c "import json,sys; print(json.load(sys.stdin)['messages'][-1]['turn'])")
INSPECT=$(curl -s "$BASE/api/v1/sessions/$SID/inspections/$CID_A/$LAST_TURN")
echo "$INSPECT" | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert '第二人称' in d['prompt'], 'pov 指令未进 prompt'
assert '1-3 句' in d['prompt'], 'density 指令未进 prompt'
print('narrative-ok')" | grep -q narrative-ok && ok "S7 视角/密度指令进 prompt（inspection）" || bad "S7 叙事指令未生效"
# 垫场：≤4 字 → 垫场条先于回复
playwright-cli fill "textarea" "嗯" >/dev/null 2>&1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '发送'); b.click(); return 'sent'; })()" >/dev/null 2>&1
sleep 3
assert_text "灯光在幕布" "S7 垫场渲染（≤4 字触发）"

say "11. 主动性（S8：成员面板开关）"
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => (x.textContent.includes('成员'))); if (!b) return 'no-btn'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "S8 成员面板打开" || bad "S8 成员面板打开失败"
sleep 1
assert_text "主动插话" "S8 插话开关渲染"
assert_text "接话" "S8 接话开关渲染"

say "12. 设置页（R48：引擎/思考）"
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '设置'); if (!t) return 'no-tab'; t.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "设置 Tab 打开" || bad "设置 Tab 未找到"
sleep 2
assert_text "推理引擎" "设置页：推理引擎卡片"
assert_text "模型思考" "设置页：思考开关卡片"
assert_text "当前生效" "设置页：当前引擎标记"
# 切引擎 → 读回标记跟随（fake 模式引擎不变，但接口/回读链路必须通）
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.includes('原生 OpenRouter')); if (!b) return 'no-engine'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "点击引擎选项" || bad "引擎选项未找到"
sleep 1
ENG=$(playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.includes('原生 OpenRouter')); return b ? b.textContent.includes('当前生效') : 'no-btn'; })()" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$ENG" == *true* ]] && ok "引擎切换读回（当前生效=原生）" || bad "引擎切换未读回: $ENG"
# 切回 DSH
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.includes('DSH')); if (!b) return 'no-dsh'; b.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "切回 DSH" || bad "DSH 选项未找到"
# 思考开关：关 → aria 变 false → 再开
playwright-cli eval "(() => { const s = Array.from(document.querySelectorAll('button[aria-pressed]')).pop(); if (!s) return 'no-switch'; s.click(); return 'ok'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q '"ok"' && ok "点击思考开关" || bad "思考开关未找到"
sleep 1
TH=$(playwright-cli eval "Array.from(document.querySelectorAll('button[aria-pressed]')).pop()?.getAttribute('aria-pressed')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$TH" == *false* ]] && ok "思考关读回（aria=false）" || bad "思考关未读回: $TH"
playwright-cli eval "(() => { const s = Array.from(document.querySelectorAll('button[aria-pressed]')).pop(); s && s.click(); return 'ok'; })()" >/dev/null 2>&1

say "13. 新建角色（表单创建，R50）"
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '角色'); if (t) t.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 2
playwright-cli eval "document.querySelector('[data-testid=\"character-create\"]')?.click()" >/dev/null 2>&1
sleep 1
EDITOR=$(playwright-cli eval "!!document.querySelector('[data-testid=\"card-name\"]')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$EDITOR" == *true* ]] && ok "新建角色弹层打开（空白编辑器）" || bad "空白编辑器未打开"
playwright-cli fill "[data-testid=\"card-name\"]" "冒烟新角色" >/dev/null 2>&1
playwright-cli fill "[data-testid=\"card-description\"]" "由表单创建的测试角色" >/dev/null 2>&1
playwright-cli fill "[data-testid=\"card-first-mes\"]" "（点头）我是新来的。" >/dev/null 2>&1
# 成功提示只存活 3s：用页面内探针（50ms 采样）捕获，避免轮询往返错过窗口
playwright-cli eval "(()=>{window.__createProbe='pending';const t0=performance.now();const iv=setInterval(()=>{const m=document.body.innerText.match(/已创建角色[^\n]{0,24}/);if(m){clearInterval(iv);window.__createProbe=m[0];}else if(performance.now()-t0>20000){clearInterval(iv);window.__createProbe='timeout';}},50);return 'installed';})()" >/dev/null 2>&1
playwright-cli eval "(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '创建角色'); if (!b) return 'no-create'; b.click(); return 'created'; })()" 2>/dev/null | grep -A1 '### Result' | grep -q created && ok "点击「创建角色」" || bad "创建按钮未找到/点击失败"
sleep 3
PROBE=$(playwright-cli eval "window.__createProbe" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$PROBE" == *"已创建角色「冒烟新角色」"* ]] && ok "创建成功提示（探针捕获）" || bad "未捕获创建提示: $PROBE"
# 弹层已关闭
MODAL=$(playwright-cli eval "!!document.querySelector('[data-testid=\"card-name\"]')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$MODAL" == *false* ]] && ok "创建后弹层已关闭" || bad "弹层未关闭"
assert_text "冒烟新角色" "角色卡片出现在列表"
curl -s "$BASE/api/v1/characters" | python3 -c "
import json,sys
cards=[c for c in json.load(sys.stdin) if c['card']['name']=='冒烟新角色']
assert len(cards)==1, cards
c=cards[0]
assert c['card']['description']=='由表单创建的测试角色', c['card']['description']
assert c['card']['first_mes']=='（点头）我是新来的。', c['card']['first_mes']
print('card-ok')" | grep -q card-ok && ok "字段读回（描述/开场白已落盘）" || bad "新建角色字段读回异常"
# 删除按钮（两步确认）→ 卡片与文件一并清理
NEW_CID=$(curl -s "$BASE/api/v1/characters" | python3 -c "
import json,sys
print(next((c['id'] for c in json.load(sys.stdin) if c['card']['name']=='冒烟新角色'),'none'))")
playwright-cli eval "(()=>{const card=[...document.querySelectorAll('[data-testid=\"character-card\"]')].find(el=>el.textContent.includes('冒烟新角色')); const btn=card?.querySelector('[data-testid=\"character-delete\"]'); if(!btn) return 'no-del'; btn.click(); return 'confirm-step';})()" 2>/dev/null | grep -A1 '### Result' | grep -q confirm-step && ok "点击删除（进入二次确认）" || bad "删除按钮未找到"
sleep 1
assert_text "确认删除" "二次确认按钮出现"
playwright-cli eval "document.querySelector('[data-testid=\"character-delete-confirm\"]')?.click()" >/dev/null 2>&1
sleep 2
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1/characters/$NEW_CID")
[[ "$CODE" == "404" ]] && ok "删除生效（API 404）" || bad "删除后 API 仍返回 $CODE"
assert_text_not "冒烟新角色" "列表已移除该角色"

say "14. 意图代笔（R40：短意图 → 右栏候选 → 覆盖填入）"
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '聊天'); if (t) t.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 2
# 先收起辅助栏，验证代笔会自动展开
playwright-cli eval "(() => { const p = document.querySelector('[data-testid=\"assist-panel\"]'); if (p) document.querySelector('[data-testid=\"assist-toggle\"]').click(); return 'collapsed'; })()" >/dev/null 2>&1
sleep 1
playwright-cli fill "textarea" "拒绝她" >/dev/null 2>&1
playwright-cli eval "document.querySelector('[data-testid=\"draft-assist\"]')?.click()" >/dev/null 2>&1
for i in $(seq 1 10); do sleep 1; playwright-cli eval "document.body.innerText.includes('代笔候选')" 2>/dev/null | grep -q true && break; done
assert_text "代笔候选" "代笔候选出现（面板自动展开）"
N_DRAFT=$(playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]').length" 2>/dev/null | grep -A1 '### Result' | tail -1 | tr -d '"')
[[ "$N_DRAFT" == "3" ]] && ok "代笔产出 3 条候选" || bad "代笔候选条数异常: $N_DRAFT"
playwright-cli eval "document.querySelectorAll('[data-testid=\"assist-candidate\"]')[0]?.click()" >/dev/null 2>&1
sleep 1
BOX_D=$(playwright-cli eval "document.querySelector('textarea').value" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$BOX_D" == *"接一句"* && "$BOX_D" != *"拒绝她"* ]] && ok "代笔候选点击=覆盖填入" || bad "代笔填入异常: $BOX_D"
REST=$(playwright-cli eval "!!document.querySelector('[data-testid=\"composer-restore-draft\"]')" 2>/dev/null | grep -A1 '### Result' | tail -1)
[[ "$REST" == *true* ]] && ok "覆盖后可恢复原意图" || bad "恢复入口未出现"
playwright-cli fill "textarea" "" >/dev/null 2>&1

say "15. 迁移包（F10.3：导出 zip → UI 导入 → 报告）"
curl -s -o /tmp/mrp-smoke-bundle.zip "$BASE/api/v1/bundle/export"
python3 - <<'PY' | grep -q bundle-ok && ok "导出迁移包（卡 + 侧车 + manifest）" || bad "导出迁移包异常"
import zipfile
z = zipfile.ZipFile('/tmp/mrp-smoke-bundle.zip')
names = z.namelist()
assert any(n.startswith('characters/') and n.endswith('.json') and '.mrp.' not in n for n in names), names
assert any(n.endswith('.mrp.json') for n in names), names
assert 'manifest.json' in names, names
print('bundle-ok')
PY
N_BEFORE=$(curl -s "$BASE/api/v1/characters" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
playwright-cli eval "(() => { const t = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '角色'); if (t) t.click(); return 'ok'; })()" >/dev/null 2>&1
sleep 2
# 点拖拽区 → 文件选择器 → 上传 zip
playwright-cli eval "(() => { const el = Array.from(document.querySelectorAll('div')).find(d => typeof d.className === 'string' && d.className.includes('border-dashed') && d.textContent.includes('拖入角色卡')); if (!el) return 'no-drop'; el.click(); return 'picked'; })()" >/dev/null 2>&1
sleep 1
playwright-cli upload /tmp/mrp-smoke-bundle.zip >/dev/null 2>&1
for i in $(seq 1 12); do sleep 1; playwright-cli eval "!!document.querySelector('[data-testid=\"bundle-report\"]')" 2>/dev/null | grep -q true && break; done
assert_text "迁移包导入报告" "导入报告面板出现"
N_AFTER=$(curl -s "$BASE/api/v1/characters" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
[[ "$N_AFTER" == "$((N_BEFORE * 2))" ]] && ok "角色翻倍（$N_BEFORE→$N_AFTER，冲突换新不覆盖）" || bad "导入后角色数异常（$N_BEFORE→$N_AFTER）"

say "清理"
playwright-cli close >/dev/null 2>&1
fuser -k ${PORT}/tcp 2>/dev/null
rm -rf /tmp/mrp-smoke-data

echo ""
echo "===== 冒烟结果: $PASS 通过 / $FAIL 失败 ====="
exit $([[ $FAIL -eq 0 ]] && echo 0 || echo 1)
