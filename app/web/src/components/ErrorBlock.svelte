<script lang="ts">
  import { t } from "../lib/i18n";
  import Icon from "./Icon.svelte";

  let { retry }: { retry: () => void } = $props();
</script>

<button class="error" type="button" onclick={retry}>
  <span>{t("error")}</span>
  <Icon name="retry" size={20} />
</button>

<style>
  .error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: calc(100% - 2 * var(--gutter));
    margin: 24px var(--gutter);
    min-height: 56px;
    padding: 14px 16px;
    border: 1px solid rgb(242 154 136 / 0.3);
    border-radius: var(--r-card);
    background: rgb(242 154 136 / 0.08);
    color: var(--failed);
    font-weight: 550;
    text-align: left;
    cursor: pointer;
  }
  .error:active { background: rgb(242 154 136 / 0.14); }
</style>
