'use strict';
const job = document.querySelector('[data-job-id]');
if (job && ['queued', 'running'].includes(job.dataset.jobStatus)) {
  const poll = async () => {
    try {
      const response = await fetch(`/api/v1/jobs/${encodeURIComponent(job.dataset.jobId)}`);
      if (!response.ok) throw new Error('Progress is unavailable. Refresh the page to retry.');
      const result = await response.json();
      const status = document.getElementById('job-status');
      status.textContent = result.status;
      status.className = `badge ${result.status}`;
      document.getElementById('job-logs').textContent = (result.logs || []).join('\n') || 'Waiting for progress…';
      if (!['queued', 'running'].includes(result.status)) {
        window.location.reload();
        return;
      }
      window.setTimeout(poll, 2000);
    } catch (error) {
      const message = document.getElementById('poll-error');
      message.textContent = error.message;
      message.hidden = false;
    }
  };
  window.setTimeout(poll, 1000);
}

const activityViewer = document.getElementById('activity-viewer');
if (activityViewer) {
  const content = document.getElementById('activity-viewer-content');
  const close = activityViewer.querySelector('.activity-viewer-close');
  let activityUrl = null;
  let activityGeneration = 0;

  const activityMain = async response => {
    if (!response.ok) throw new Error('Could not start this activity. Try again.');
    const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
    const main = parsed.getElementById('main');
    if (!main) throw new Error('The activity result could not be displayed.');
    return main.innerHTML;
  };
  const showError = error => {
    content.replaceChildren();
    const message = document.createElement('p');
    message.className = 'notice error';
    message.textContent = error.message;
    content.append(message);
    if (!activityViewer.open) activityViewer.showModal();
  };
  const refreshActivity = async generation => {
    if (!activityViewer.open || generation !== activityGeneration || !activityUrl) return;
    try {
      const response = await fetch(activityUrl);
      const html = await activityMain(response);
      if (!activityViewer.open || generation !== activityGeneration) return;
      content.innerHTML = html;
      const current = content.querySelector('[data-job-status]');
      if (current && ['queued', 'running'].includes(current.dataset.jobStatus)) {
        window.setTimeout(() => refreshActivity(generation), 1500);
      }
    } catch (error) {
      if (activityViewer.open && generation === activityGeneration) showError(error);
    }
  };
  const closeViewer = () => activityViewer.close();
  close.addEventListener('click', closeViewer);
  activityViewer.addEventListener('click', event => {
    if (event.target === activityViewer) closeViewer();
  });
  activityViewer.addEventListener('close', () => {
    activityGeneration += 1;
    activityUrl = null;
  });

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target || link.hasAttribute('download')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || !/^\/activity\/[^/]+$/.test(url.pathname)) return;
    event.preventDefault();
    const generation = ++activityGeneration;
    activityUrl = url.href;
    content.innerHTML = '<p class="muted">Loading activity…</p>';
    if (!activityViewer.open) activityViewer.showModal();
    refreshActivity(generation);
  });

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const submitter = event.submitter;
    const action = submitter?.hasAttribute('formaction') ? submitter.formAction : form.action;
    const url = new URL(action, window.location.href);
    const fullPageActions = new Set(['/actions/library.scan', '/actions/ratings.sync']);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/actions/') || fullPageActions.has(url.pathname)) return;
    event.preventDefault();
    if (url.pathname === '/actions/entry.play') document.getElementById('song-viewer')?.close();
    const generation = ++activityGeneration;
    activityUrl = null;
    content.innerHTML = '<p class="muted">Submitting activity…</p>';
    if (!activityViewer.open) activityViewer.showModal();
    if (submitter) submitter.disabled = true;
    try {
      const data = new FormData(form);
      if (submitter?.name && !data.has(submitter.name)) data.append(submitter.name, submitter.value);
      const response = await fetch(action, {method: 'POST', body: data});
      if (!response.redirected || !new URL(response.url).pathname.startsWith('/activity/')) {
        throw new Error('The activity did not return a progress view.');
      }
      activityUrl = response.url;
      const html = await activityMain(response);
      if (!activityViewer.open || generation !== activityGeneration) return;
      content.innerHTML = html;
      const current = content.querySelector('[data-job-status]');
      if (current && ['queued', 'running'].includes(current.dataset.jobStatus)) {
        window.setTimeout(() => refreshActivity(generation), 1500);
      }
    } catch (error) {
      if (generation === activityGeneration) showError(error);
    } finally {
      if (submitter?.isConnected) submitter.disabled = false;
    }
  });
}

const append = (parent, tag, text) => {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.append(element);
  return element;
};
const songLine = (song, suffix = '') => {
  const line = document.createElement('span');
  line.className = 'song-line';
  const link = append(line, 'a', song);
  link.className = 'song-details-link';
  link.href = `/song?${new URLSearchParams({song})}`;
  if (suffix) append(line, 'span', suffix);
  const form = document.createElement('form');
  form.method = 'post';
  form.action = '/actions/song.play';
  for (const [name, value] of [
    ['csrf_token', document.querySelector('meta[name="csrf-token"]').content],
    ['song', song],
  ]) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.append(input);
  }
  const button = append(form, 'button', 'Play MPD');
  button.className = 'quiet song-play';
  const stream = append(line, 'a', '▶');
  stream.className = 'quiet stream-link';
  stream.href = `/stream?${new URLSearchParams({song})}`;
  stream.dataset.streamTitle = song;
  stream.setAttribute('aria-label', `Stream ${song}`);
  stream.title = 'Play in browser';
  line.append(form);
  return line;
};

const viewToggle = document.getElementById('playlist-view-toggle');
if (viewToggle) {
  const editorForm = document.getElementById('playlist-editor');
  const editor = document.getElementById('playlist-content');
  const playable = document.getElementById('playlist-playable');
  const trackList = document.getElementById('playlist-playable-tracks');
  viewToggle.addEventListener('click', () => {
    const showPlayable = playable.hidden;
    if (showPlayable) {
      trackList.replaceChildren();
      const tracks = editor.value.split(/\r?\n/).flatMap(raw => {
        const line = raw.trim();
        if (!line || line.startsWith('#')) return [];
        const match = line.match(/^(.+?)\s+[-\u2013\u2014]\s+(.+)$/);
        return match ? [`${match[1].trim()} - ${match[2].trim()}`] : [];
      });
      if (tracks.length) {
        tracks.forEach(track => {
          const item = document.createElement('li');
          item.append(songLine(track));
          trackList.append(item);
        });
      } else {
        append(trackList, 'li', 'No playable song lines found.').className = 'empty';
      }
    }
    editorForm.hidden = showPlayable;
    playable.hidden = !showPlayable;
    viewToggle.textContent = showPlayable ? 'Editable Text' : 'Playable Links';
    viewToggle.setAttribute('aria-pressed', String(showPlayable));
  });
}

const statsButton = document.getElementById('playlist-stats');
if (statsButton) {
  const editor = document.getElementById('playlist-content');
  const status = document.getElementById('playlist-stats-status');
  const details = document.getElementById('playlist-stats-details');
  const duration = seconds => {
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor(total % 3600 / 60);
    const s = total % 60;
    return `${h ? h + 'h ' : ''}${m}m ${s}s`;
  };
  let snapshot = null;
  let running = false;
  editor.addEventListener('input', () => {
    if (snapshot !== null && !running) {
      status.textContent = 'Editor changed. Click Stats to recalculate.';
    }
  });
  const render = r => {
    const summary = document.createElement('dl');
    summary.className = 'result-list';
    const rows = [
      ['Songs (including repeats)', r.song_count],
      ['Unique songs', r.unique_songs],
      ['Duplicate entries (extra copies)', r.duplicates],
      ['Unique artists', r.unique_artists],
      ['Genres', (r.genres || []).length],
      ['Entries with genre tags', `${r.genre_entries || 0} of ${r.song_count}`],
      ['Library matches without genre (unique songs)', r.unknown_genre_songs || 0],
      ['Duration (known tracks, including repeats)', r.timed_entries ? duration(r.duration_seconds) : r.song_count ? 'Unavailable' : duration(0)],
      ['Duration without repeats (known tracks)', r.timed_entries ? duration(r.unique_duration_seconds) : r.song_count ? 'Unavailable' : duration(0)],
      ['Entries with known duration', `${r.timed_entries} of ${r.song_count}`],
      ['Average song length (known entries)', r.timed_entries ? duration(r.duration_seconds / r.timed_entries) : 'Unavailable'],
      ['Library matches (unique songs)', r.matched_songs],
      ['Not found in library (unique songs)', r.missing_songs.length],
      ['Library matches without duration', r.unknown_duration_songs],
      ['Songs not checked', r.unchecked_songs],
      ['Comment lines', r.comment_lines],
      ['Blank lines', r.blank_lines],
      ['Unrecognized lines', r.invalid_lines.length],
    ];
    for (const [label, value] of rows) {
      append(summary, 'dt', label);
      append(summary, 'dd', String(value));
    }
    details.append(summary);
    append(details, 'h3', 'Genre breakdown');
    append(details, 'p', 'Counts use genre tags from the selected library files. Percentages include repeats and use all playlist song entries. Songs with multiple genre tags count toward each genre, so percentages may exceed 100% in total.');
    if ((r.genres || []).length) {
      const wrapper = document.createElement('div');
      wrapper.className = 'table-scroll';
      const table = document.createElement('table');
      const head = table.createTHead().insertRow();
      for (const label of ['Genre', 'Unique songs', 'Entries', '% of playlist']) {
        append(head, 'th', label).scope = 'col';
      }
      const body = table.createTBody();
      for (const genre of r.genres) {
        const row = body.insertRow();
        for (const value of [genre.genre, genre.songs, genre.entries, `${(100 * genre.entries / r.song_count).toFixed(1)}%`]) {
          append(row, 'td', String(value));
        }
      }
      wrapper.append(table);
      details.append(wrapper);
    } else {
      append(details, 'p', 'No genre tags available for the matched songs.');
    }
    append(details, 'p', 'Duplicates ignore capitalization and spacing around the artist/title separator. Library estimates use Apollo’s best-file matching, before rating filters; published playback may differ.');
    if (r.timed_entries < r.song_count) append(details, 'p', 'Duration is incomplete: some entries could not be timed.').className = 'notice';
    if (r.warning) append(details, 'p', r.warning).className = 'notice';
    const list = (heading, entries, playable = false) => {
      if (!entries.length) return;
      const section = document.createElement('details');
      append(section, 'summary', heading);
      const ul = document.createElement('ul');
      ul.className = 'tracks';
      entries.forEach(entry => {
        const li = document.createElement('li');
        if (playable) li.append(songLine(entry.song, entry.suffix));
        else li.textContent = entry;
        ul.append(li);
      });
      section.append(ul);
      details.append(section);
    };
    list('Top artists (including repeats)', r.top_artists.map(a => `${a.artist}: ${a.count}`));
    list('Duplicate songs', r.duplicate_songs.map(s => ({song: s.song, suffix: ` — ${s.count} copies`})), true);
    list('Missing songs', r.missing_songs.map(song => ({song, suffix: ''})), true);
    list('Unrecognized line numbers', r.invalid_lines.map(n => `Line ${n}`));
    list('File formats (unique matched songs)', Object.entries(r.formats).map(([format, count]) => `${format.toUpperCase()}: ${count}`));
    for (const key of ['shortest', 'longest']) {
      if (r[key]) {
        const paragraph = append(details, 'p', `${key === 'shortest' ? 'Shortest' : 'Longest'} known song: `);
        paragraph.append(songLine(r[key].song, ` (${duration(r[key].duration)})`));
      }
    }
  };
  statsButton.addEventListener('click', async () => {
    snapshot = editor.value;
    running = true;
    statsButton.disabled = true;
    document.getElementById('playlist-stats-results').hidden = false;
    details.replaceChildren();
    status.textContent = 'Calculating stats…';
    try {
      const response = await fetch(statsButton.dataset.url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content},
        body: JSON.stringify({action: 'playlist.stats', payload: {name: statsButton.dataset.name, content: snapshot}}),
      });
      if (!response.ok) throw new Error('Could not start stats. Reload the page or try again.');
      const submitted = await response.json();
      const activity = append(details, 'a', 'View calculation progress');
      activity.href = `/activity/${encodeURIComponent(submitted.job.id)}`;
      const poll = async () => {
        try {
          const response = await fetch(submitted.status_url);
          if (!response.ok) throw new Error('Stats are unavailable. Try again or check Activity.');
          const result = await response.json();
          if (['queued', 'running'].includes(result.status)) {
            status.textContent = result.status === 'queued' ? 'Stats queued. Waiting for the worker…' : 'Calculating library matches and duration…';
            window.setTimeout(poll, 1500);
            return;
          }
          if (!result.result || result.status === 'failed') throw new Error(result.error || 'Stats calculation failed.');
          details.replaceChildren();
          render(result.result);
          status.textContent = editor.value === snapshot ? 'Stats calculated.' : 'Stats calculated for earlier text. Editor changed; click Stats to recalculate.';
        } catch (error) {
          status.textContent = error.message;
        }
        running = false;
        statsButton.disabled = false;
      };
      await poll();
    } catch (error) {
      status.textContent = error.message;
      running = false;
      statsButton.disabled = false;
    }
  });
}


const songViewer = document.getElementById('song-viewer');
if (songViewer) {
  const content = document.getElementById('song-viewer-content');
  let generation = 0;
  document.getElementById('song-viewer-close').addEventListener('click', () => songViewer.close());
  songViewer.addEventListener('click', event => {
    if (event.target === songViewer) songViewer.close();
  });
  songViewer.addEventListener('close', () => { generation += 1; });
  document.addEventListener('click', async event => {
    const link = event.target.closest('a.song-details-link');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const current = ++generation;
    content.textContent = 'Loading song details…';
    if (!songViewer.open) songViewer.showModal();
    try {
      const response = await fetch(link.href);
      if (!response.ok) throw new Error('Song details unavailable. Check service connectivity and try again.');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      const main = parsed.getElementById('main');
      if (!main) throw new Error('Song details could not be loaded. Reload the page and try again.');
      if (current === generation && songViewer.open) content.innerHTML = main.innerHTML;
    } catch (error) {
      if (current === generation && songViewer.open) content.textContent = error.message;
    }
  });
}

const playerTemplate = document.getElementById('inline-player-template');
if (playerTemplate) {
  const player = playerTemplate.content.firstElementChild.cloneNode(true);
  const audio = new Audio();
  audio.preload = 'metadata';
  const toggle = player.querySelector('[data-stream-toggle]');
  const seek = player.querySelector('[data-stream-seek]');
  const time = player.querySelector('[data-stream-time]');
  const status = player.querySelector('[data-stream-status]');
  let activeLink = null;
  let generation = 0;
  const formatTime = value => {
    const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  };
  const update = () => {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    seek.max = duration;
    seek.disabled = duration <= 0;
    seek.value = audio.currentTime;
    seek.setAttribute('aria-valuetext', `${formatTime(audio.currentTime)} of ${formatTime(duration)}`);
    time.textContent = `${formatTime(audio.currentTime)} / ${formatTime(duration)}`;
    toggle.textContent = audio.paused ? 'Play' : 'Pause';
  };
  const play = async () => {
    const current = generation;
    try {
      await audio.play();
      if (current === generation) status.textContent = '';
    } catch (error) {
      if (current === generation && activeLink && error.name !== 'AbortError') {
        status.textContent = error.name === 'NotAllowedError'
          ? 'Press Play to start.'
          : 'Audio unavailable or unsupported by your browser.';
      }
    }
  };
  const release = () => {
    generation += 1;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    activeLink?.removeAttribute('aria-current');
    activeLink = null;
    player.remove();
  };
  toggle.addEventListener('click', () => audio.paused ? play() : audio.pause());
  player.querySelector('[data-stream-stop]').addEventListener('click', () => {
    generation += 1;
    audio.pause();
    if (audio.readyState > 0) audio.currentTime = 0;
    status.textContent = '';
    update();
  });
  seek.addEventListener('input', () => {
    audio.currentTime = Number(seek.value);
    update();
  });
  for (const event of ['loadedmetadata', 'durationchange', 'timeupdate', 'play', 'pause', 'ended', 'emptied']) {
    audio.addEventListener(event, update);
  }
  audio.addEventListener('waiting', () => { if (!audio.paused) status.textContent = 'Buffering…'; });
  audio.addEventListener('playing', () => { status.textContent = ''; });
  audio.addEventListener('error', () => {
    if (activeLink && audio.error) status.textContent = 'Audio unavailable or unsupported by your browser.';
  });
  // Activity refreshes can replace the row containing the active player.
  new MutationObserver(() => {
    if (activeLink && !activeLink.isConnected) release();
  }).observe(document.getElementById('main').parentNode, {childList: true, subtree: true});
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('close', () => {
      if (activeLink && dialog.contains(activeLink)) release();
    });
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a.stream-link');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    generation += 1;
    audio.pause();
    activeLink?.removeAttribute('aria-current');
    activeLink = link;
    link.setAttribute('aria-current', 'true');
    link.before(player);
    player.setAttribute('aria-label', `Browser playback: ${link.dataset.streamTitle}`);
    status.textContent = 'Loading…';
    audio.src = link.href;
    update();
    play();
  });
}
