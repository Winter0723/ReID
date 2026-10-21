<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showConfirmDialog } from "vant";
import { api, post, money, date, API, type Visit } from "../../shared/api";
import VisitorEditor from "./VisitorEditor.vue";
import { invokePayment, type Payment } from "../orders/wechat";
const route = useRoute(),
  visit = ref<Visit>(),
  error = ref(""),
  busy = ref(false),
  orientation = ref("landscape"),
  paymentMessage = ref("");
const editRequested = ref(false);
const router = useRouter();
function closeEditor() {
  if (visit.value?.stage === "ready") editRequested.value = false;
  else router.push("/visits");
}
const editorVisible = computed(
  () =>
    !!visit.value &&
    !visit.value.order &&
    (["selecting", "editing"].includes(visit.value.stage) ||
      (visit.value.stage === "review" && visit.value.selection_available) ||
      (visit.value.stage === "ready" && editRequested.value)),
);
function editorUpdated(next: Visit) {
  visit.value = next;
  editRequested.value = next.stage === "editing";
}
async function retryProduction() {
  busy.value = true;
  try {
    visit.value = await post<Visit>(`/visits/${route.params.id}/retry`);
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
type TestCheckout = {
  provider: "test";
  token: string;
  amount_cents: number;
  currency: string;
};
const testCheckout = ref<TestCheckout>();
const outcomes = [
  ["success", "成功"],
  ["cancel", "取消"],
  ["failure", "失败"],
  ["delayed", "延迟到账（15 秒）"],
  ["duplicate", "重复通知"],
];
async function simulate(outcome: string) {
  if (!testCheckout.value) return;
  busy.value = true;
  try {
    visit.value = await api<Visit>(`/visits/${route.params.id}/test-payment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: testCheckout.value.token, outcome }),
    });
    paymentMessage.value = (
      {
        success: "模拟付款已确认。",
        cancel: "已取消支付，订单已保留。",
        failure: "模拟支付失败，可重新尝试。",
        delayed: "模拟付款已受理，15 秒后刷新到账结果。",
        duplicate: "重复通知已处理，订单保持已支付。",
      } as Record<string, string>
    )[outcome];
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
let timer: ReturnType<typeof setTimeout> | undefined,
  alive = true;
const manual = computed(
  () =>
    visit.value?.order?.payment_provider === "visitor_manual" ||
    (!visit.value?.order && visit.value?.payment_provider === "manual"),
);
const paid = computed(() => visit.value?.order?.status === "paid");
const inWechat = /MicroMessenger/i.test(navigator.userAgent);
const videoUrl = computed(
  () =>
    `${API}/visits/${route.params.id}/video/${orientation.value}/${paid.value ? "full" : "preview"}`,
);
const stages = [
  ["sealing", "封存录像"],
  ["waiting", "整理素材"],
  ["matching", "寻找画面"],
  ["planning", "编排故事"],
  ["rendering", "制作视频"],
];
const activeIndex = computed(() =>
  stages.findIndex((s) => s[0] === visit.value?.stage),
);
const processing = computed(() => activeIndex.value >= 0);
async function load() {
  try {
    const next = await api<Visit>(`/visits/${route.params.id}`);
    if (alive) {
      visit.value = next;
      error.value = "";
    }
  } catch (e) {
    if (alive) {
      error.value = (e as Error).message;
      if ((e as { status?: number }).status === 401) visit.value = undefined;
    }
  }
}
async function poll() {
  await load();
  if (alive)
    timer = setTimeout(poll, visit.value?.stage === "ready" && !processing.value && (!manual.value || !visit.value.order || paid.value) ? 15000 : 2000);
}
onMounted(poll);
onUnmounted(() => {
  alive = false;
  clearTimeout(timer);
});
async function finish() {
  try {
    await showConfirmDialog({
      title: "结束本次游览？",
      message: "我们将匹配本次游览的画面，随后由你挑选片段、制作旅行短片。",
      confirmButtonText: "结束并匹配",
      confirmButtonColor: "#214e42",
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    visit.value = await post<Visit>(`/visits/${route.params.id}/finish`);
    error.value = "";
  } catch (e) {
    // A lost response does not undo an accepted finish request.
    await load();
    if (!visit.value || visit.value.stage === "visiting")
      error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function refreshPayment() {
  try {
    visit.value = await post<Visit>(
      `/visits/${route.params.id}/payment-status`,
    );
    paymentMessage.value = paid.value
      ? "付款已确认，可以领取视频。"
      : manual.value
        ? "等待工作人员确认，请稍后刷新。"
        : "付款结果确认中，请稍后刷新。";
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function pay() {
  busy.value = true;
  error.value = "";
  try {
    const result = await post<{
      visit: Visit;
      payment: Payment | TestCheckout | { provider: "manual" } | null;
    }>(`/visits/${route.params.id}/pay`);
    visit.value = result.visit;
    if (result.payment) {
      if (
        "provider" in result.payment &&
        result.payment.provider === "manual"
      ) {
        paymentMessage.value =
          "申请已提交，等待景区工作人员确认。确认后即可下载领取。";
        return;
      }
      if ("provider" in result.payment && result.payment.provider === "test") {
        testCheckout.value = result.payment;
        return;
      }
      const outcome = await invokePayment(result.payment as Payment);
      paymentMessage.value =
        outcome === "cancel" ? "已取消支付，订单已保留。" : "正在确认付款结果…";
      await refreshPayment();
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function claim() {
  busy.value = true;
  try {
    visit.value = await post<Visit>(`/visits/${route.params.id}/claim`);
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <RouterLink to="/visits" class="back">← 我的游览</RouterLink>
  <div v-if="error" class="notice error" role="alert">
    {{ error }}<button class="text-button" @click="load">重试</button>
  </div>
  <section
    v-if="testCheckout"
    class="notice test-checkout"
    aria-label="模拟支付"
  >
    <h2>模拟支付</h2>
    <p>测试环境，不产生真实扣款</p>
    <p>
      订单 {{ visit?.order?.order_no }} · {{ money(testCheckout.amount_cents) }}
    </p>
    <button
      v-for="item in outcomes"
      :key="item[0]"
      class="secondary"
      :disabled="busy || (item[0] === 'duplicate' ? !paid : paid)"
      @click="simulate(item[0])"
    >
      {{ item[1] }}
    </button>
    <button class="text-button" :disabled="busy" @click="refreshPayment">
      刷新到账结果
    </button>
    <button class="text-button" @click="testCheckout = undefined">
      关闭收银台
    </button>
    <p role="status">{{ paymentMessage }}</p>
  </section>
  <template v-if="visit"
    ><h1 class="page-title">
      {{
        paid
          ? "已购视频"
          : visit.stage === "ready"
            ? "视频试看"
            : visit.stage === "visiting"
              ? "我的游览"
              : editorVisible
                ? "我的短片工作室"
                : "匹配与制作进度"
      }}
    </h1>
    <template v-if="visit.stage === 'visiting'"
      ><section class="notice feature">
        <span class="status-mark">✓</span>
        <h2>您已进入{{ visit.name }}</h2>
        <small>开始时间</small
        ><strong class="time">{{ date(visit.created_at) }}</strong>
        <p>祝您游览愉快，愿这段旅程留下美好的回忆。</p>
      </section>
      <button class="primary" :disabled="busy" @click="finish">
        结束游览，匹配我的画面
      </button>
      <p class="caption center">
        匹配成功后，由你挑选画面并制作短片。
      </p></template
    >
    <VisitorEditor
      v-else-if="editorVisible"
      :visit-id="visit.id"
      @updated="editorUpdated"
      @close="closeEditor"
    />
    <template v-else-if="visit.stage !== 'ready'"
      ><div class="notice feature" :aria-busy="processing">
        <span class="status-mark" :class="{ spinning: processing }" aria-hidden="true">◷</span>
        <h2>{{ visit.message }}</h2>
        <div v-if="processing && !visit.render_progress?.length" class="processing-wait" role="progressbar" :aria-label="visit.message" aria-valuetext="正在处理中">
          <span />
        </div>
        <p v-if="processing" role="status">正在处理，请稍候…进度会自动更新。</p>
        <p>您可以关闭页面，稍后重新打开查看制作进度。</p>
      </div>
      <div v-if="visit.render_progress?.length" class="notice">
        <div v-for="item in visit.render_progress" :key="item.orientation">
          <p>
            {{ item.orientation === "landscape" ? "横版" : "竖版" }} ·
            {{ item.message }} · {{ Math.round(item.progress * 100) }}%
          </p>
          <progress
            :value="item.progress"
            max="1"
            style="width: 100%; accent-color: #214e42"
          />
        </div>
      </div>
      <ol class="stages">
        <li
          v-for="(stage, index) in stages"
          :key="stage[0]"
          :class="{ done: index < activeIndex, active: index === activeIndex }"
        >
          <span>{{ index < activeIndex ? "✓" : index + 1 }}</span>
          <div>
            <b>{{ stage[1] }}</b
            ><small>{{
              visit.stage === "review"
                ? "可自行重试"
                : index < activeIndex
                  ? "已完成"
                  : index === activeIndex
                    ? "正在进行"
                    : "等待中"
            }}</small>
          </div>
        </li>
      </ol>
      <div v-if="visit.stage === 'review'" class="notice">
        <h2>本次处理未完成</h2>
        <p>
          暂未找到可用画面或视频制作未完成，请重新匹配或重试制作。
        </p>
        <button class="primary" :disabled="busy" @click="retryProduction">
          重新尝试
        </button>
        <small>游览编号 {{ visit.id }}</small>
      </div></template
    >
    <template v-else
      ><div v-if="paid" class="notice feature">
        <h2>感谢您的支持</h2>
        <p>这段旅程，值得被永久珍藏。</p>
      </div>
      <div v-if="visit.expired" class="notice error">
        领取有效期已结束，请联系景区服务台。
      </div>
      <video
        v-else
        :key="videoUrl"
        :src="videoUrl"
        controls
        playsinline
        preload="metadata"
        class="video"
        :class="orientation"
        @error="error = '视频暂时无法加载，请检查网络后重试'"
      />
      <fieldset class="orientation">
        <legend>切换画面方向</legend>
        <button
          v-for="item in [
            ['landscape', '横屏'],
            ['portrait', '竖屏'],
          ]"
          :key="item[0]"
          :aria-pressed="orientation === item[0]"
          @click="orientation = item[0]"
        >
          {{ item[1] }}
        </button>
      </fieldset>
      <button
        v-if="!visit.order"
        class="secondary"
        @click="editRequested = true"
      >
        重新挑选与编排
      </button>
      <template v-if="!paid"
        ><p class="caption">
          {{
            manual
              ? "当前播放的是试看版，工作人员确认后可领取横竖双版完整视频。"
              : "当前播放的是试看版，购买后可领取横竖双版完整视频。"
          }}
        </p>
        <p v-if="manual" class="notice">
          付款由景区工作人员在后台确认，本页面不会发起微信扣款。
        </p>
        <button
          class="primary"
          :disabled="busy || (manual && !!visit.order)"
          @click="pay"
        >
          {{
            busy
              ? "正在确认订单…"
              : manual
                ? visit.order
                  ? "申请已提交，等待工作人员确认"
                  : `申请付款确认 · ${money(visit.price_cents)}`
                : `购买完整视频 · ${money(visit.price_cents)}`
          }}</button
        ><button v-if="visit.order" class="text-button" @click="refreshPayment">
          {{ manual ? "刷新工作人员确认结果" : "已付款？刷新支付结果" }}
        </button>
        <p v-if="paymentMessage" role="status">
          {{ paymentMessage }}
        </p></template
      >
      <template v-else
        ><a
          v-if="!visit.expired"
          :href="
            (inWechat && visit.delivery_page_url) || visit.download_urls?.[orientation] || `${videoUrl}?download=true`
          "
          class="primary download"
          >下载视频</a
        >
        <a v-if="!visit.expired && visit.delivery_page_url" :href="visit.delivery_page_url" class="secondary download">打开双版领取页</a>
        <div class="notice">
          <h2>如何保存到手机</h2>
          <p>
            请先点击“打开双版领取页”。如果微信未开始下载，在领取页右上角选择“在浏览器打开”，再点击保存。专用领取页无需再次微信登录，可以下载横竖两个版本。
          </p>
          <p>建议下载横竖两个版本，保存后再确认领取。</p>
        </div>
        <button
          v-if="!visit.expired"
          class="secondary"
          :disabled="busy || visit.order?.claimed"
          @click="claim"
        >
          {{
            visit.order?.claimed ? "已确认领取" : "我已保存，确认领取"
          }}</button
        ><small class="order-detail"
          >订单 {{ visit.order?.order_no }}<br />领取有效期至
          {{ date(visit.order!.expires_at) }}</small
        ></template
      >
    </template>
  </template>
  <p v-else-if="!error" role="status">正在读取游览记录…</p>
</template>
<style scoped>
.spinning { animation: spin 1.5s linear infinite; }
.processing-wait { width: 100%; height: 8px; margin: 20px 0 12px; border-radius: 8px; overflow: hidden; background: #dfe8e2; }
.processing-wait span { display: block; width: 40%; height: 100%; border-radius: inherit; background: #214e42; animation: wait 1.6s ease-in-out infinite alternate; }
@keyframes wait { from { transform: translateX(-90%); } to { transform: translateX(250%); } }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinning, .processing-wait span { animation: none; } }
</style>
