import { SurfaceDialog } from '../design-system/SurfaceDialog';
import { useEffect, useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { lanApi } from '../features/settings/lanApi';
import { useCharacterStore } from '../store/characterStore';
import { useLorebookStore } from '../store/lorebookStore';
import { useChatStore } from '../store/chatStore';
import { useAppearanceStore } from '../appearance/store';
import { PointerEffects } from '../appearance/PointerEffects';
import { beginLoad } from '../utils/loadDiagnostics';
import { LanSessionExpiredError } from '../api/lanSession';
import { AppRoutes } from './AppRoutes';
function LanReauthOverlay() {
  const [open, setOpen] = useState(false);
  const [accessCode, setAccessCode] = useState("");
  const [remember, setRemember] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const show = () => {
      setOpen(true);
      setError("");
    };
    window.addEventListener("mrp:lan-session-expired", show);
    return () => window.removeEventListener("mrp:lan-session-expired", show);
  }, []);
  if (!open) return null;
  const pair = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await lanApi.pair(accessCode, remember);
      const result = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      if (!response.ok) {
        setError(result.detail ?? "配对失败，请检查访问码后重试。");
        return;
      }
      setAccessCode("");
      setOpen(false);
      window.dispatchEvent(new CustomEvent("mrp:lan-session-restored"));
    } catch {
      setError(
        "无法连接电脑服务，请确认电脑仍在运行且手机与电脑处于同一网络。",
      );
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <SurfaceDialog title={"需要重新配对"} className="lan-session-shade" onClose={() => setOpen(false)} busy={submitting} nested={false}>
      <section
        className="lan-session-dialog"
       
       
        aria-labelledby="lan-session-title"
      >
        <span className="lan-session-kicker">手机访问</span>
        <h2 id="lan-session-title">需要重新配对</h2>
        <p>
          本次请求没有执行。当前页面和输入内容仍保留；配对后请手动重试，生成请求不会自动重发。
        </p>
        <form onSubmit={(event) => void pair(event)}>
          <label htmlFor="lan-session-code">电脑上显示的访问码</label>
          <input
            id="lan-session-code"
            value={accessCode}
            onChange={(event) => setAccessCode(event.target.value)}
            autoComplete="one-time-code"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={48}
            required
            autoFocus
          />
          <label className="lan-session-remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            <span>
              <strong>记住这台手机</strong>
              <small>
                闲置 30 天 / 累计 90 天失效；取消后为闲置 7 天 / 累计 30 天。
              </small>
            </span>
          </label>
          {error && (
            <p className="lan-session-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" disabled={submitting || !accessCode.trim()}>
            {submitting ? "正在配对…" : "配对并继续"}
          </button>
        </form>
        <small className="lan-session-foot">
          会话已过期时，未完成的生成不会自动恢复。确认配对后，请检查页面再决定是否重试。
        </small>
      </section>
    </SurfaceDialog>
  );
}

export function Bootstrap() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const viewport = window.visualViewport;
    const syncViewportHeight = () =>
      document.documentElement.style.setProperty(
        "--mrp-visual-height",
        `${Math.ceil(viewport?.height ?? window.innerHeight)}px`,
      );
    syncViewportHeight();
    window.addEventListener("resize", syncViewportHeight);
    viewport?.addEventListener("resize", syncViewportHeight);
    void useAppearanceStore
      .getState()
      .initialize()
      .catch(() => undefined);
    const syncLocation = () =>
      navigate(`${window.location.pathname}${window.location.search}`, {
        replace: true,
      });
    window.addEventListener("mrp:location", syncLocation);
    return () => {
      window.removeEventListener("resize", syncViewportHeight);
      viewport?.removeEventListener("resize", syncViewportHeight);
      window.removeEventListener("mrp:location", syncLocation);
    };
  }, [navigate]);
  // Resources are useful to these routes, but never block the application shell.
  useEffect(() => {
    let live = true;
    setError("");
    const jobs: { label: string; request: Promise<void> }[] = [];
    if (
      !pathname.startsWith("/simple-chats") &&
      !pathname.startsWith("/settings")
    ) {
      jobs.push({
        label: "角色资料",
        request: useCharacterStore.getState().load(),
      });
      if (!pathname.includes("/branches/") && !pathname.endsWith("/worldline"))
        jobs.push({
          label: "世界书",
          request: useLorebookStore.getState().load(),
        });
    }
    if (
      pathname === "/" ||
      pathname.startsWith("/stories") ||
      pathname.startsWith("/sessions")
    )
      jobs.push({
        label: "故事列表",
        request: useChatStore.getState().loadSessions(),
      });
    void Promise.allSettled(jobs.map((job) => job.request)).then((results) => {
      if (!live) return;
      const failed = results.flatMap((result, index) =>
        result.status === "rejected" &&
        !(result.reason instanceof LanSessionExpiredError)
          ? [jobs[index].label]
          : [],
      );
      setError(
        failed.length
          ? `${failed.join("、")}读取失败。当前页面仍可使用已加载的资料。`
          : "",
      );
    });
    return () => {
      live = false;
    };
  }, [pathname, retry]);
  useEffect(() => () => useChatStore.getState().disconnect(), []);
  useEffect(() => {
    const finish = beginLoad("render", pathname);
    const frame = requestAnimationFrame(() => finish());
    return () => cancelAnimationFrame(frame);
  }, [pathname]);
  return (
    <>
      {error && (
        <div className="v7-bootstrap-error" role="alert">
          {error}{" "}
          <button type="button" onClick={() => setRetry((value) => value + 1)}>
            重新读取资料
          </button>
        </div>
      )}
      <AppRoutes />
      <LanReauthOverlay />
      <PointerEffects />
    </>
  );
}

