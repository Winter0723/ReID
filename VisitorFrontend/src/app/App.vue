<script setup lang="ts">
import { onErrorCaptured, onMounted, ref } from "vue";
import { useSession } from "../features/auth/session";
const session = useSession();
const error = ref("");
onMounted(async () => {
  try { await session.loadRuntime(); }
  catch (e) { error.value = (e as Error).message; }
});
onErrorCaptured(() => {
  error.value = "页面暂时无法显示，请刷新重试";
  return false;
});
</script>
<template>
  <div class="app-shell">
    <div v-if="session.runtime?.auth_provider === 'browser'" class="test-banner" role="status">浏览器登录模式：请使用当前浏览器访问游览记录。</div>
    <div v-if="session.runtime?.testing" class="test-banner" role="status">{{ session.runtime.payment_provider !== "wechat" ? "测试环境，不产生真实扣款" : "测试登录已启用，微信支付将产生真实扣款" }}</div>
    <header class="brand">
      <RouterLink to="/visits">景区留影</RouterLink
      ><span>游览影像记录<br />与个人短片制作</span>
    </header>
    <main>
      <div v-if="error" class="notice error" role="alert">
        {{ error }}<a href="">刷新页面</a>
      </div>
      <RouterView v-else-if="session.runtime" :key="$route.path" />
      <p v-else role="status">正在读取服务配置…</p>
    </main>
    <nav class="bottom-nav" aria-label="主导航">
      <RouterLink to="/visits">我的游览</RouterLink
      ><RouterLink to="/works">我的作品</RouterLink>
    </nav>
  </div>
</template>
