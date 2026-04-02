<script lang="ts">
  import type { Note } from '$lib/types';
  import { createNote } from '$lib/api';

	export let serverId: number;
	export let onCreated: (note: Note) => void;

	let username = '';
	let sshPassword = '';
	let content  = '';
	let loading  = false;
	let error    = '';

	async function handleSubmit() {
		if (!username.trim() || !sshPassword.trim() || !content.trim()) return;
		loading = true;
		error   = '';
		try {
			const note = await createNote(serverId, username.trim(), sshPassword.trim(), content.trim());
			onCreated(note);
			content = '';
		} catch (e) {
      error = e instanceof Error ? e.message : '작성 실패';
    } finally {
      loading = false;
    }
  }
</script>

<div class="note-form flex flex-col gap-2 pt-2">
  <!-- Name + password row -->
  <div class="note-form-row flex gap-2">
    <input
      type="text"
      placeholder="이름"
      bind:value={username}
      class="note-form-input flex-1 min-w-0 rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-white/20"
    />
    <input
      type="password"
      placeholder="비밀번호"
      bind:value={sshPassword}
      class="note-form-input flex-1 min-w-0 rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-white/20"
    />
  </div>

  <!-- Content textarea -->
  <textarea
    placeholder="내용"
    bind:value={content}
    rows="2"
    class="note-form-textarea w-full rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-white/20 resize-none"
  ></textarea>

  <!-- Error + submit row -->
  <div class="note-form-footer flex items-center justify-between gap-2">
    {#if error}
      <span class="text-xs text-red-400">{error}</span>
    {:else}
      <span></span>
    {/if}
    <button
      on:click={handleSubmit}
      disabled={loading}
      class="note-form-submit btn-ghost text-xs px-3 py-1 active:scale-95 disabled:opacity-40 shrink-0"
    >
      {#if loading}
        <span class="inline-block w-3 h-3 border border-white/30 border-t-white/80 rounded-full animate-spin"></span>
      {:else}
        작성
      {/if}
    </button>
  </div>
</div>
