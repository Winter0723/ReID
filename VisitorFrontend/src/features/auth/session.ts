import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError, API } from "../../shared/api";
export const useSession = defineStore("visitor-session", () => {
  const authenticated = ref(false);
  const runtime = ref<{ auth_provider: string; payment_provider: string; testing: boolean }>();
  async function loadRuntime() {
    runtime.value = await api("/runtime");
  }
  async function testLogin(projectId: number, visitor: string, inviteCode: string) {
    await api("/auth/test", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, visitor, invite_code: inviteCode }) });
    authenticated.value = true;
  }
  async function browserLogin(projectId: number) {
    await api("/auth/browser", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }) });
    authenticated.value = true;
  }
  async function check() {
    try {
      await api("/me");
      authenticated.value = true;
    } catch (error) {
      authenticated.value = false;
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
    }
    return authenticated.value;
  }
  function login(projectId: number) {
    window.location.assign(`${API}/auth/wechat?project_id=${projectId}`);
  }
  return { authenticated, runtime, loadRuntime, testLogin, browserLogin, check, login };
});
