import { Component, type ReactNode, type ErrorInfo } from 'react';
export class RouteErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("页面路由渲染失败", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="v7-route-error" role="alert">
        <section className="v7-route-error-card">
          <span className="v7-eyebrow">页面加载异常</span>
          <h1>这个页面没有正常打开</h1>
          <p>
            页面脚本加载失败或渲染时发生错误。重新加载会重新获取页面资源；如果仍然失败，请查看错误详情。
          </p>
          <div className="v7-route-error-actions">
            <button
              type="button"
              className="v7-btn v7-btn-primary"
              onClick={() => window.location.reload()}
            >
              重新加载页面
            </button>
            <button
              type="button"
              className="v7-btn v7-btn-soft"
              onClick={() => window.location.assign("/")}
            >
              回到故事首页
            </button>
          </div>
          <details>
            <summary>查看错误详情</summary>
            <pre>{this.state.error.message}</pre>
          </details>
        </section>
      </main>
    );
  }
}

