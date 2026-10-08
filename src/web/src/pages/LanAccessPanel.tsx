import { Button } from "../design-system";
import {
  lanApi,
  lanQueries,
  type LanDeviceSession,
} from "../features/settings/lanApi";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Copy,
  Download,
  RefreshCw,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

const sessionCleanupWarning =
  "本进程内权限已撤销，但磁盘清理未确认；如果失效标记也无法写入，下次启动可能恢复旧摘要。停止服务后手动删除 %LOCALAPPDATA%\\Sekai o Tsumugu Hime\\lan-sessions.json 和同名 .invalid 文件，再启用 LAN。";

const formatDate = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString();
const formatDuration = (seconds: number) => {
  if (seconds < 60) return "不足 1 分钟";
  const minutes = Math.ceil(seconds / 60);
  const days = Math.floor(minutes / (24 * 60));
  if (days > 0) return `${days} 天`;
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} 小时 ${remainder} 分钟` : `${hours} 小时`;
};

export default function LanAccessPanel() {
  const overview = useQuery(lanQueries.overview());
  const status = overview.data?.status ?? null;
  const sessions = overview.data?.sessions ?? [];
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");

  const [deviceAction, setDeviceAction] = useState(false);
  const [rotatedCode, setRotatedCode] = useState("");
  const [certificateDownload, setCertificateDownload] = useState(false);

  const loopbackManagementPage = [
    "localhost",
    "127.0.0.1",
    "::1",
    "[::1]",
  ].includes(window.location.hostname.toLowerCase());

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await overview.refetch();
      if (result.error) throw result.error;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取局域网状态");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(value);
      window.setTimeout(
        () => setCopied((current) => (current === value ? "" : current)),
        1800,
      );
    } catch {
      setError(`复制${label}失败，请手动选择内容。`);
    }
  };

  const downloadRootCertificate = async () => {
    setCertificateDownload(true);
    setError("");
    try {
      const certificate = await lanApi.certificate();
      const downloadUrl = URL.createObjectURL(certificate);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = `sekai-lan-root-${status?.root_ca_fingerprint_sha256?.slice(0, 12).toLowerCase() ?? "ca"}.crt`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法下载根证书");
    } finally {
      setCertificateDownload(false);
    }
  };

  const revokeDevice = async (session: LanDeviceSession) => {
    if (!window.confirm(`撤销“${session.device_name}”的访问权限？`)) return;
    setDeviceAction(true);
    setError("");
    try {
      const response = await lanApi.revoke(session.session_id);
      if (!response.ok) throw new Error(`撤销失败（${response.status}）`);
      const data = (await response.json().catch(() => ({}))) as {
        session_state_cleanup_confirmed?: boolean;
      };
      await refresh();
      if (data.session_state_cleanup_confirmed === false)
        setError(sessionCleanupWarning);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法撤销设备");
    } finally {
      setDeviceAction(false);
    }
  };

  const revokeAllDevices = async () => {
    if (
      !window.confirm(
        "立即撤销所有已配对手机的访问权限？当前手机会断开，访问码仍保持不变。",
      )
    )
      return;
    setDeviceAction(true);
    setError("");
    try {
      const response = await lanApi.revokeAll();
      if (!response.ok) throw new Error(`撤销失败（${response.status}）`);
      const data = (await response.json().catch(() => ({}))) as {
        session_state_cleanup_confirmed?: boolean;
      };
      await refresh();
      if (data.session_state_cleanup_confirmed === false)
        setError(sessionCleanupWarning);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法撤销设备");
    } finally {
      setDeviceAction(false);
    }
  };

  const rotateCode = async () => {
    if (!window.confirm("轮换访问码会让所有已配对手机立即失效。继续？")) return;
    setDeviceAction(true);
    setError("");
    try {
      const response = await lanApi.rotate();
      const data = (await response.json().catch(() => ({}))) as {
        access_code?: string;
        session_state_cleanup_confirmed?: boolean;
      };
      if (!response.ok || !data.access_code)
        throw new Error(`轮换访问码失败（${response.status}）`);
      setRotatedCode(data.access_code);
      await refresh();
      if (data.session_state_cleanup_confirmed === false)
        setError(sessionCleanupWarning);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法轮换访问码");
    } finally {
      setDeviceAction(false);
    }
  };

  const closeLan = async () => {
    if (
      !window.confirm(
        "关闭会撤销所有手机会话、停止选定网卡上的远程监听，并请求删除本项目防火墙规则。电脑本机页面会继续使用；Windows 可能显示管理员授权提示。继续？",
      )
    )
      return;
    setDeviceAction(true);
    setError("");
    try {
      const response = await lanApi.close();
      const data = (await response.json().catch(() => ({}))) as {
        listener_stopped?: boolean;
        firewall_rule_removed?: boolean | null;
        session_state_cleanup_confirmed?: boolean;
        detail?: string | null;
      };
      if (!response.ok)
        throw new Error(data.detail || `关闭失败（${response.status}）`);
      setRotatedCode("");
      await refresh();
      if (!data.listener_stopped) {
        setError(
          "无法确认远程监听器已停止。请在项目目录运行 stop_server.bat。 ",
        );
      } else if (!data.session_state_cleanup_confirmed) {
        setError(sessionCleanupWarning);
      } else if (!data.firewall_rule_removed) {
        setError(
          data.detail ||
            "远程监听和会话已关闭，但防火墙规则清理未确认。再次启用 LAN 前请检查规则。",
        );
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法关闭局域网监听");
    } finally {
      setDeviceAction(false);
    }
  };

  return (
    <>
      <div className="v7-settings-title">
        <Smartphone size={22} />
        <div>
          <h2>手机访问</h2>
          <p>让同一 Wi-Fi 或个人热点上的手机浏览器连接这台电脑。</p>
        </div>
      </div>
      <div className="v7-settings-card v7-lan-card">
        <div className="v7-lan-state">
          <span
            className={`v7-lan-state-dot ${status?.enabled && status.https_available && status.firewall_rule_verified ? "is-on" : ""}`}
          />
          <div>
            <strong>
              {loading
                ? "正在读取状态…"
                : status?.enabled
                  ? status.https_available
                    ? status.firewall_rule_verified
                      ? "LAN HTTPS 监听与防火墙规则已核验"
                      : "LAN HTTPS 已建立，正在核验防火墙规则"
                    : "LAN HTTPS 尚未建立；手机入口不可用"
                  : "局域网访问已关闭"}
            </strong>
            <small>
              {status?.enabled
                ? `绑定 ${status.interfaces[0]?.name ?? status.bind_address} (${status.bind_address}) · TCP ${status.port} · 手机连通性和公网可达性未验证`
                : "普通启动只允许本机访问"}
            </small>
          </div>
          <Button
            variant="secondary"
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            aria-label="刷新局域网地址"
          >
            <RefreshCw size={16} /> 刷新
          </Button>
        </div>

        {error && (
          <p className="v7-lan-error" role="alert">
            {error}
          </p>
        )}

        {status?.enabled &&
          (!status.https_available || !status.firewall_rule_verified) && (
            <p className="v7-lan-local-only">
              LAN
              服务正在等待启动器核验防火墙规则。核验完成前不要分享手机访问地址。
            </p>
          )}

        {!status?.enabled && !loading && (
          <div className="v7-lan-launch">
            <h3>在电脑上启动 LAN 入口</h3>
            <p>
              在项目目录双击 <code>start_lan.bat</code>
              。普通启动入口不会打开局域网。
            </p>
            <p>
              启动窗口会显示可用 URL 和访问码。端口若由本项目 MRP
              占用，会先询问是否切换；其它程序占用时会退出且不停止它。
            </p>
            <p>
              启动器会让你选择一块物理网卡，只在该 IPv4
              地址和子网监听，并验证精确的防火墙规则。拒绝授权或核验失败时，LAN
              服务会停止启动。
            </p>
          </div>
        )}

        {status?.enabled &&
          status.https_available &&
          status.firewall_rule_verified && (
            <>
              <div className="v7-lan-address-list">
                <div className="v7-lan-section-head">
                  <div>
                    <h3>手机连接地址</h3>
                    <p>
                      使用经本机根 CA 验证的 HTTPS，只向所选的{" "}
                      {status.interfaces[0]?.name ?? status.bind_address}{" "}
                      网卡提供服务，允许客户端网段为{" "}
                      {status.interfaces[0]?.subnet ?? "未提供"}。二维码只包含
                      URL，不包含访问码。
                    </p>
                  </div>
                </div>
                {status.interfaces.length === 0 ? (
                  <div className="v7-lan-empty">
                    没有发现活动的物理网卡 IPv4。请连接 Wi-Fi
                    或个人热点后点“刷新”。
                  </div>
                ) : (
                  status.interfaces.map((item) => (
                    <article className="v7-lan-address" key={item.ip}>
                      <div
                        className="v7-lan-qr"
                        aria-label={`访问 ${item.ip} 的二维码`}
                      >
                        <QRCodeSVG
                          value={item.url}
                          size={184}
                          marginSize={4}
                          level="M"
                        />
                      </div>
                      <div className="v7-lan-address-copy">
                        <span className="v7-lan-ip">{item.ip}</span>
                        <code>{item.url}</code>
                        <Button
                          variant="secondary"
                          type="button"
                          onClick={() => void copy(item.url, "地址")}
                        >
                          {copied === item.url ? (
                            <Check size={16} />
                          ) : (
                            <Copy size={16} />
                          )}
                          {copied === item.url ? "已复制" : "复制地址"}
                        </Button>
                      </div>
                    </article>
                  ))
                )}
              </div>

              <div className="v7-lan-howto">
                <div>
                  <ShieldCheck size={18} />
                  <strong>首次配对</strong>
                </div>
                <ol>
                  <li>
                    手机和电脑连接同一个可信 Wi-Fi，或让电脑连接手机热点。
                  </li>
                  <li>
                    首次连接前先在电脑本机打开此设置页，下载下方显示的公共根证书，并通过
                    Android 的“安装证书 / CA 证书”设置导入。Android
                    通常会要求设置锁屏 PIN 或密码。只安装你确认指纹相同的证书。
                  </li>
                  <li>
                    安装后重新启动 Chrome，扫描上方二维码；确认地址栏显示 HTTPS
                    和安全连接标志，再输入 LAN
                    启动窗口显示的访问码。不要忽略证书或安全警告。
                  </li>
                  <li>
                    配对 cookie 由浏览器保存，设为 Secure、HttpOnly 和
                    SameSite=Strict。默认“记住这台手机”为闲置{" "}
                    {status.session_policies.remembered.idle_days} 天 / 累计{" "}
                    {status.session_policies.remembered.absolute_days}{" "}
                    天；取消勾选为闲置{" "}
                    {status.session_policies.normal.idle_days} 天 / 累计{" "}
                    {status.session_policies.normal.absolute_days} 天。
                  </li>
                  <li>
                    会话摘要保存在当前 Windows
                    用户的应用数据目录中，常规服务重启后可继续使用；更换网卡、地址或子网后需重新配对。
                  </li>
                </ol>
                <p>
                  访问码只在启动窗口显示一次，不会保存在浏览器、URL、二维码或服务器日志中。
                </p>
              </div>

              <div className="v7-lan-certificate">
                <div>
                  <h3>手机信任证书</h3>
                  <p>
                    公钥根证书只用于验证这台电脑的 LAN HTTPS 服务。电脑本机 TLS
                    已由启动器使用该 CA 完成验证；Android 手机仍需手动安装。
                  </p>
                  <code>
                    SHA-256：{status.root_ca_fingerprint_sha256 ?? "不可用"}
                  </code>
                  <small>
                    当前服务器证书有效期至：
                    {status.certificate_expires_at
                      ? new Date(status.certificate_expires_at).toLocaleString()
                      : "不可用"}
                  </small>
                </div>
                {status.can_manage_devices && loopbackManagementPage ? (
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() => void downloadRootCertificate()}
                    disabled={
                      certificateDownload || !status.root_ca_fingerprint_sha256
                    }
                  >
                    <Download size={16} />{" "}
                    {certificateDownload ? "正在下载…" : "下载公共根证书"}
                  </Button>
                ) : (
                  <p className="v7-lan-local-only">
                    请在电脑浏览器打开{" "}
                    <code>http://127.0.0.1:{status.port}/</code>{" "}
                    下载公共根证书。手机端无法导出证书。
                  </p>
                )}
                <p>
                  安装指南：Android 设置中的“证书”或“凭据存储”页面选择“CA
                  证书”，导入刚下载的 <code>.crt</code> 文件。系统菜单会随
                  Android / ColorOS 版本变化；安装完成后完全退出并重新打开
                  Chrome，再确认 HTTPS
                  安全标志。证书变化或指纹不一致时不要继续连接。
                </p>
              </div>

              <div className="v7-lan-warning">
                <strong>选择可信网络</strong>
                <p>
                  LAN 链路已使用 HTTPS 加密并验证服务器身份；仍只在你控制的私人
                  Wi-Fi
                  或个人热点使用。不要安装来源不明的根证书。共享或公共网络不适合使用；路由器端口映射和公网可达性尚未验证。
                </p>
              </div>

              <div className="v7-lan-troubleshoot">
                <h3>手机打不开时</h3>
                <ol>
                  <li>
                    先确认手机与电脑在同一 Wi-Fi/热点、Android
                    已安装显示指纹对应的根证书，并直接使用上方完整 HTTPS URL。
                  </li>
                  <li>
                    若 Chrome 仍提示连接不安全，停止操作并在电脑重新核对根证书
                    SHA-256
                    指纹；不要跳过警告或安装第二个不同证书。可先退出并重新打开
                    Chrome。
                  </li>
                  <li>
                    确认电脑没有睡眠、LAN 服务仍在运行；更换 Wi-Fi
                    或热点后先关闭 LAN，再重新运行 <code>start_lan.bat</code>{" "}
                    选择新网络。
                  </li>
                  <li>
                    启动器会核验只针对实际 Python 程序、TCP
                    端口、所选本地地址、网卡和客户端子网的 Windows
                    防火墙规则。若授权被拒绝或规则核验失败，启动器会停止 LAN
                    服务；再次运行 <code>start_lan.bat</code> 并接受防火墙授权。
                  </li>
                  <li>
                    手机热点可能开启客户端隔离；若同一热点的设备互相不可见，请改用路由器
                    Wi-Fi。
                  </li>
                </ol>
              </div>

              {status.can_manage_devices ? (
                <div className="v7-lan-local-only">
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() => void closeLan()}
                    disabled={deviceAction}
                  >
                    {deviceAction ? "正在关闭…" : "立即关闭局域网访问"}
                  </Button>
                  <p>
                    关闭会撤销全部手机会话并停止远程监听；电脑本机页面会继续使用。Windows
                    可能要求管理员授权删除防火墙规则。
                  </p>
                </div>
              ) : (
                <p className="v7-lan-local-only">
                  手机不能停止电脑上的监听器。请在电脑本机打开设置页并点击“立即关闭局域网访问”。
                </p>
              )}
            </>
          )}

        {status?.enabled && (
          <section className="v7-lan-devices">
            <div className="v7-lan-devices-heading">
              <div>
                <h3>已配对设备</h3>
                <p>
                  每台设备的期限取决于配对时的选择。设备名称来自浏览器，可被伪造，仅用于识别。会话状态存储
                  {status.session_store_available
                    ? "没有已知错误"
                    : status.session_state_cleanup_confirmed
                      ? "有错误；部分会话可能会在服务重启后要求重新配对"
                      : "未能确认清理；请先按下方提示手动清理，重启可能恢复旧摘要"}
                  。
                </p>
              </div>
              {status.can_manage_devices && (
                <div className="v7-lan-device-actions">
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() => void revokeAllDevices()}
                    disabled={deviceAction || sessions.length === 0}
                  >
                    撤销全部
                  </Button>
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() => void rotateCode()}
                    disabled={deviceAction}
                  >
                    轮换访问码
                  </Button>
                </div>
              )}
            </div>
            {rotatedCode && status.can_manage_devices && (
              <div className="v7-lan-rotated-code" role="status">
                <strong>新访问码（仅显示在此页面）</strong>
                <code>{rotatedCode}</code>
                <Button
                  variant="secondary"
                  type="button"
                  onClick={() => void copy(rotatedCode, "访问码")}
                >
                  {copied === rotatedCode ? (
                    <Check size={16} />
                  ) : (
                    <Copy size={16} />
                  )}
                  {copied === rotatedCode ? "已复制" : "复制访问码"}
                </Button>
                <small>
                  轮换已撤销所有手机会话。关闭或重新载入此页面后不会再显示此访问码。
                </small>
              </div>
            )}
            {status.can_manage_devices ? (
              sessions.length === 0 ? (
                <p className="v7-lan-device-empty">目前没有已配对手机。</p>
              ) : (
                <ul className="v7-lan-device-list">
                  {sessions.map((session) => (
                    <li key={session.session_id}>
                      <div className="v7-lan-device-info">
                        <strong>{session.device_name}</strong>
                        <small>
                          {session.ip} · 配对于 {formatDate(session.created_at)}
                        </small>
                        <small>
                          {session.remembered ? "已记住此设备" : "常规会话"} ·
                          最后活动 {formatDate(session.last_activity_at)} ·
                          绝对剩余 {formatDuration(session.expires_in_seconds)}{" "}
                          · 闲置剩余{" "}
                          {formatDuration(session.idle_expires_in_seconds)}
                        </small>
                      </div>
                      <Button
                        variant="secondary"
                        type="button"
                        onClick={() => void revokeDevice(session)}
                        disabled={deviceAction}
                      >
                        撤销
                      </Button>
                    </li>
                  ))}
                </ul>
              )
            ) : (
              <p className="v7-lan-device-empty">
                请在电脑本机打开此设置页管理手机会话。
              </p>
            )}
          </section>
        )}
      </div>
    </>
  );
}
