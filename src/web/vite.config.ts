import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { productionManualChunks, productionRouterEntry } from './build/productionChunks.mjs';
import { regularInterfaceIcons } from './build/regularInterfaceIcons.mjs';
import { cascadeLayerOrder } from './build/cascadeLayerOrder.mjs';

// 真后端就绪后，SSE 与 REST 走这里的反代：
// dev 模式下 /api/* → 本机 API（可用 VITE_API_TARGET 指定 loopback 端口）
// 后端会校验写请求来源，因此代理只把本地开发页面的 Origin 改写为本地 API。
const configuredApiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000';
const parsedApiTarget = new URL(configuredApiTarget);
if (parsedApiTarget.protocol !== 'http:' || !['localhost', '127.0.0.1', '::1'].includes(parsedApiTarget.hostname)) {
  throw new Error('VITE_API_TARGET must be an HTTP loopback URL.');
}
const apiTarget = parsedApiTarget.origin;

export default defineConfig(({ command }) => ({
  resolve: command === 'build' ? { alias: [{ find: /^react-router$/, replacement: productionRouterEntry() }] } : undefined,
  build: { rollupOptions: { output: { manualChunks: productionManualChunks, experimentalMinChunkSize: 20000, chunkFileNames: "assets/[hash].js" } } },
  plugins: [cascadeLayerOrder(), react(), regularInterfaceIcons()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq, req) => {
            const origin = req.headers.origin;
            if (!origin) return;
            try {
              const parsed = new URL(origin);
              if (parsed.protocol === 'http:' && ['localhost', '127.0.0.1', '::1'].includes(parsed.hostname)) {
                proxyReq.setHeader('origin', apiTarget);
              }
            } catch {
              // Preserve malformed origins so the backend rejects them.
            }
          });
        },
      },
    },
  },
}));

