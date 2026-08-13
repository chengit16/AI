# Web 应用

本目录承载 React 19、TypeScript 和 Vite Web 应用。动态路由最终由已发布菜单快照生成，前端权限只改善使用体验，服务端始终执行独立授权。

本地启动：

```bash
pnpm --filter @ai-platform/web dev
```

默认访问地址为 `http://127.0.0.1:3000/status`，开发服务器把 `/api` 请求代理到 `http://127.0.0.1:8000`。
