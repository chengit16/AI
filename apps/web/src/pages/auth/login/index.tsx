import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Form, Input, Segmented } from "antd";
import { ArrowRight, LockKeyhole, UserRound } from "lucide-react";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import { loginWithPassword, registerPersonalAccount } from "@/api/services/auth";
import { PlatformMark } from "@/components/PlatformMark/PlatformMark";
import { useSessionStore } from "@/store/session";

import styles from "./LoginPage.module.css";

interface LoginForm {
  loginName: string;
  password: string;
}

interface RegistrationForm extends LoginForm {
  displayName: string;
}

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
    <main className={styles.page}>
      <section className={styles.identity} aria-labelledby="platform-title">
        <PlatformMark />
        <div>
          <p className={styles.kicker}>LOCAL AI WORKSPACE</p>
          <h1 id="platform-title">把知识、权限与 AI 工作流放在一个可信空间里</h1>
          <p>个人空间开箱即用，企业空间保留组织、成员和套餐治理能力。</p>
        </div>
        <dl className={styles.facts}>
          <div>
            <dt>运行方式</dt>
            <dd>本地 Docker</dd>
          </div>
          <div>
            <dt>空间模型</dt>
            <dd>个人 + 企业</dd>
          </div>
          <div>
            <dt>安全边界</dt>
            <dd>Session + Workspace</dd>
          </div>
        </dl>
      </section>

      <section className={styles.formArea} aria-labelledby="auth-title">
        <div className={styles.formPanel}>
          <div className={styles.formHeading}>
            <span className={styles.formIcon}>
              <LockKeyhole size={20} />
            </span>
            <div>
              <h2 id="auth-title">进入平台</h2>
              <p>使用本地账号建立可信工作空间上下文</p>
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
          {error && <Alert className={styles.alert} type="error" showIcon message={error} />}
          <Form<LoginForm & Partial<RegistrationForm>>
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
              className={styles.submit}
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
