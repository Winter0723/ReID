import { ref } from "vue";
import { defineStore } from "pinia";

import type { UserProfile } from "../lib/api/auth";
import { readToken } from "../lib/api/http";
import type { LoginForm } from "./types";


function emptyLoginForm(): LoginForm {
  return {
    account: "",
    password: "",
    email: "",
    username: "",
    code: "",
    registerPassword: "",
    debugCode: "",
  };
}

export const useAuthStore = defineStore("auth", () => {
  const coreAccessToken = ref(readToken("core"));
  const scenicAccessToken = ref(readToken("scenic"));
  const coreUser = ref<UserProfile | null>(null);
  const scenicUser = ref<UserProfile | null>(null);
  const coreLogin = ref<LoginForm>(emptyLoginForm());
  const scenicLogin = ref<LoginForm>(emptyLoginForm());

  function updateSession(scope: "core" | "scenic", accessToken: string, user: UserProfile | null = null): void {
    if (scope === "core") {
      coreAccessToken.value = accessToken;
      coreUser.value = user;
    } else {
      scenicAccessToken.value = accessToken;
      scenicUser.value = user;
    }
  }

  return {
    coreAccessToken,
    scenicAccessToken,
    coreUser,
    scenicUser,
    coreLogin,
    scenicLogin,
    updateSession,
  };
});
