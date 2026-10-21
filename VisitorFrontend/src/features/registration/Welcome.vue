<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, money, type Site } from "../../shared/api";
import { useSession } from "../auth/session";
const route = useRoute(),
  router = useRouter(),
  session = useSession();
const site = ref<Site>(),
  error = ref(""),
  busy = ref(false),
  inviteCode = ref(""),
  testVisitor = ref("A");
async function load() {
  error.value = "";
  try {
    site.value = await api<Site>(`/sites/${route.params.id}`);
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function start() {
  if (!site.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (!session.runtime) await session.loadRuntime();
    if (session.runtime?.auth_provider === "test") {
      await session.testLogin(site.value.id, testVisitor.value, inviteCode.value);
      inviteCode.value = "";
      await router.push(`/sites/${site.value.id}/register`);
    } else if (session.runtime?.auth_provider === "browser") {
      await session.browserLogin(site.value.id);
      await router.push(`/sites/${site.value.id}/register`);
    } else if (await session.check())
      await router.push(`/sites/${site.value.id}/register`);
    else session.login(site.value.id);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>
<template>
  <section class="welcome-hero">
    <h1>游览影像登记</h1>
    <p>{{ site?.description || "登记后可查看本次游览的匹配片段，并申请制作个人短片。" }}</p>
    <img :src="'/visitor/images/mountains.svg'" alt="层叠青山与山间溪流" />
  </section>
  <div v-if="error" class="notice error" role="alert">
    {{ error }}<button class="text-button" @click="load">重新加载</button>
  </div>
  <template v-if="site"
    ><div class="service-points">
      <div><b>园内拍摄</b><small>记录游览片段</small></div>
      <div><b>个人短片</b><small>支持横版与竖版</small></div>
      <div><b>预览后购买</b><small>确认作品后付款</small></div>
    </div>
    <div class="price-line">
      <div>
        <b>{{ site.name }}</b
        ><small>完整视频 · 登记免费</small>
      </div>
      <strong>{{ money(site.price_cents) }}</strong>
    </div>
    <fieldset v-if="session.runtime?.auth_provider === 'test'" class="test-login">
      <legend>测试账号登录</legend>
      <label>测试邀请码<input v-model="inviteCode" type="password" autocomplete="off" maxlength="256" /></label>
      <label>测试游客<select v-model="testVisitor"><option value="A">游客 A</option><option value="B">游客 B</option></select></label>
      <p class="caption">测试账号由持有同一邀请码的人员共享，请勿填写真实个人信息。</p>
    </fieldset>
    <button class="primary" :disabled="busy || !session.runtime" @click="start">
      {{ busy ? "正在登录…" : "开始登记" }}
    </button>
    <p v-if="session.runtime?.auth_provider === 'browser'" class="caption center">无需微信授权，可直接使用浏览器登记。请用同一浏览器查看记录；清除 Cookie 或更换设备后无法找回。</p>
    <p v-else class="caption center">{{ session.runtime?.auth_provider === "test" ? "可在电脑浏览器登记；切换游客请返回本景区入口。" : "请在微信中打开，登记后可找回本次游览。" }}</p>
  </template>
  <p v-else-if="!error" role="status">正在读取景区信息…</p>
</template>
