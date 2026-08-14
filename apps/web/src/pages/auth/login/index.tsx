/** 登录与个人账号注册入口，建立服务端 Session 和默认个人空间上下文。 */
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Form, Input, Segmented } from "antd";
import { ArrowRight, LockKeyhole, UserRound } from "lucide-react";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import { loginWithPassword, registerPersonalAccount } from "@/api/services/auth";
import { PlatformMark } from "@/components/PlatformMark/PlatformMark";
import { useSessionStore } from "@/store/session";

interface LoginForm {
  loginName: string;
  password: string;
}

interface RegistrationForm extends LoginForm {
  displayName: string;
}

/**
 * 提供本地账号登录与个人账号创建流程。
 *
 * 浏览器只保存账号、当前空间与 CSRF Token；Session Cookie、身份校验和默认个人空间
 * 一致性均由服务端负责，缺少个人空间时页面失败关闭而不进入业务路由。
 */
export default function LoginPage() {
  const navigate = useNavigate();
  const workspaceId = useSessionStore((state) => state.workspaceId);
  const setAuthenticated = useSessionStore((state) => state.setAuthenticated);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const login = useMutation({
    mutationFn: ({ loginName, password }: LoginForm) =>
      loginWithPassword({ login_name: loginName, password }),
    onSuccess: (response) => {
      if (!response.personal_workspace_id) {
        setError("账号缺少默认个人空间，请联系平台管理员");
        return;
      }
      setAuthenticated(response.account_id, response.personal_workspace_id, response.csrf_token);
      navigate("/workspace/overview", { replace: true });
    },
    onError: (reason) => setError(errorMessage(reason)),
  });
  // 注册事实由后端一次事务创建；成功后再登录，以取得与普通登录完全一致的 Session。
  const register = useMutation({
    mutationFn: async ({ loginName, displayName, password }: RegistrationForm) => {
      await registerPersonalAccount({
        login_name: loginName,
        display_name: displayName,
        password,
      });
      return loginWithPassword({ login_name: loginName, password });
    },
    onSuccess: (response) => {
      if (!response.personal_workspace_id) {
        setError("账号缺少默认个人空间，请联系平台管理员");
        return;
      }
      setAuthenticated(response.account_id, response.personal_workspace_id, response.csrf_token);
      navigate("/workspace/overview", { replace: true });
    },
    onError: (reason) => setError(errorMessage(reason)),
  });

  if (workspaceId) return <Navigate to="/workspace/overview" replace />;
  const pending = login.isPending || register.isPending;

  return (
    <main className="grid min-h-[100dvh] grid-cols-[minmax(340px,0.9fr)_minmax(460px,1.1fr)] bg-surface tablet-down:block tablet-down:bg-canvas">
      <section
        className="flex flex-col justify-between gap-16 border-r-[5px] border-r-solid border-accent bg-nav-bg p-[clamp(32px,6vw,76px)] text-nav-text-strong tablet-down:min-h-[310px] tablet-down:gap-8 tablet-down:border-b-4 tablet-down:border-b-solid tablet-down:border-r-0 tablet-down:p-6"
        aria-labelledby="platform-title"
      >
        <PlatformMark />
        <div>
          <p className="mb-4 mt-0 text-xs font-800 text-accent">LOCAL AI WORKSPACE</p>
          <h1
            className="m-0 max-w-[620px] text-[clamp(34px,4vw,54px)] leading-[1.18] tablet-down:text-[30px]"
            id="platform-title"
          >
            把知识、权限与 AI 工作流放在一个可信空间里
          </h1>
          <p className="mb-0 mt-6 max-w-[560px] text-[17px] leading-[1.8] text-nav-text tablet-down:text-[15px]">
            个人空间开箱即用，企业空间保留组织、成员和套餐治理能力。
          </p>
        </div>
        <dl className="m-0 grid grid-cols-3 border-t border-t-solid border-nav-separator tablet-down:hidden">
          <div className="pr-3 pt-5">
            <dt className="text-xs text-nav-muted">运行方式</dt>
            <dd className="mb-0 ml-0 mr-0 mt-2 font-700">本地 Docker</dd>
          </div>
          <div className="pr-3 pt-5">
            <dt className="text-xs text-nav-muted">空间模型</dt>
            <dd className="mb-0 ml-0 mr-0 mt-2 font-700">个人 + 企业</dd>
          </div>
          <div className="pr-3 pt-5">
            <dt className="text-xs text-nav-muted">安全边界</dt>
            <dd className="mb-0 ml-0 mr-0 mt-2 font-700">Session + Workspace</dd>
          </div>
        </dl>
      </section>

      <section
        className="grid place-items-center bg-canvas p-8 tablet-down:px-5 tablet-down:py-8"
        aria-labelledby="auth-title"
      >
        <div className="w-[min(430px,100%)]">
          <div className="mb-6 flex items-center gap-4">
            <span className="ui-icon-badge h-11 w-11">
              <LockKeyhole size={20} />
            </span>
            <div>
              <h2 className="m-0 text-2xl" id="auth-title">
                进入平台
              </h2>
              <p className="mb-0 mt-1 text-[13px] text-text-muted">
                使用本地账号建立可信工作空间上下文
              </p>
            </div>
          </div>
          <Segmented
            block
            value={mode}
            options={[
              { label: "登录", value: "login" },
              { label: "创建个人账号", value: "register" },
            ]}
            onChange={(value) => {
              setMode(value as "login" | "register");
              setError(null);
            }}
          />
          {error && <Alert className="!my-4" type="error" showIcon message={error} />}
          <Form<LoginForm & Partial<RegistrationForm>>
            className="!mt-6"
            layout="vertical"
            requiredMark={false}
            onFinish={(values) => {
              setError(null);
              if (mode === "login") login.mutate(values);
              else register.mutate(values as RegistrationForm);
            }}
          >
            {mode === "register" && (
              <Form.Item
                label="显示名称"
                name="displayName"
                rules={[{ required: true, message: "请输入显示名称" }, { max: 120 }]}
              >
                <Input
                  prefix={<UserRound size={16} />}
                  autoComplete="name"
                  placeholder="你的名称"
                />
              </Form.Item>
            )}
            <Form.Item
              label="登录名"
              name="loginName"
              rules={[{ required: true, message: "请输入登录名" }, { min: 3 }, { max: 255 }]}
            >
              <Input
                autoFocus={mode === "login"}
                autoComplete="username"
                placeholder="name@example.com"
              />
            </Form.Item>
            <Form.Item
              label="密码"
              name="password"
              rules={
                mode === "register"
                  ? [
                      { required: true, message: "请输入密码" },
                      { min: 12, message: "密码至少 12 个字符" },
                    ]
                  : [{ required: true, message: "请输入密码" }]
              }
            >
              <Input.Password
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder={mode === "register" ? "至少 12 个字符" : "输入密码"}
              />
            </Form.Item>
            <Button
              className="!mt-2 !min-h-11 !w-full"
              type="primary"
              htmlType="submit"
              loading={pending}
              icon={<ArrowRight size={17} />}
              iconPlacement="end"
            >
              {mode === "login" ? "登录" : "创建并进入"}
            </Button>
          </Form>
        </div>
      </section>
    </main>
  );
}
