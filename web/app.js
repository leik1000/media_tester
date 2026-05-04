const { createApp, ref, reactive, watch, onMounted, computed } = Vue;

const app = createApp({
    setup() {
        const tab = ref('image');
        const isSubmitting = ref(false);
        const currentLogs = ref([]);
        const currentResult = ref(null);
        const results = ref([]);
        const selectedResult = ref(null);
        const imageFiles = ref([]);
        const videoFiles = ref([]);

        const isLoading = computed(() =>
            results.value.some(item => item.status === 'starting' || item.status === 'running')
        );

        const config = reactive({
            baseUrl: 'https://api.pixellelabs.com',
            proxyUrl: 'http://127.0.0.1:10808',
            defaultApiKey: '',
            gptImage2ApiKey: '',
            gemini3ProImageApiKey: '',
            gemini31FlashImageApiKey: '',
            videoApiKey: ''
        });

        const image = reactive({
            model: 'gemini-3-pro-image-preview',
            prompt: 'A cinematic mountain sunrise with drifting clouds',
            aspectRatio: '16:9',
            size: '2K',
            quality: 'medium',
            imageUrls: ''
        });

        const video = reactive({
            model: 'sora2',
            prompt: 'A cinematic hummingbird flying through a sunlit garden',
            aspectRatio: '16:9',
            duration: 4,
            createPath: '/v1/videos',
            statusPath: '/v1/videos/{task_id}',
            imageUrls: ''
        });

        onMounted(async () => {
            const saved = localStorage.getItem('mediaTesterConfigPro');
            if (saved) {
                try {
                    const parsed = JSON.parse(saved);
                    Object.assign(config, parsed.config || {});
                    if (!config.defaultApiKey && parsed.config?.apiKey) {
                        config.defaultApiKey = parsed.config.apiKey;
                    }
                    Object.assign(image, parsed.image || {});
                    Object.assign(video, parsed.video || {});
                } catch (e) {
                    console.error('Failed to load config', e);
                }
            }
            await loadPersistedAssets();
        });


        const loadPersistedAssets = async () => {
            try {
                const res = await fetch('/api/assets');
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || 'Failed to load assets');
                results.value = (data.assets || []).map(item => ({
                    logs: [`[System] Loaded from downloads/${item.filename}.`],
                    ...item,
                }));
                if (results.value.length) {
                    currentResult.value = results.value[0];
                    currentLogs.value = currentResult.value.logs || [];
                }
            } catch (e) {
                console.error('Failed to load persisted assets', e);
            }
        };

        watch([config, image, video], () => {
            localStorage.setItem('mediaTesterConfigPro', JSON.stringify({
                config, image, video
            }));
        }, { deep: true });

        const scrollLogs = () => {
            const logContainer = document.querySelector('.overflow-auto.font-mono');
            if (logContainer) {
                setTimeout(() => {
                    logContainer.scrollTop = logContainer.scrollHeight;
                }, 10);
            }
        };

        const pushLog = (msg) => {
            currentLogs.value.push(msg);
            scrollLogs();
        };

        const submitTask = async () => {
            if (isSubmitting.value) return;
            isSubmitting.value = true;

            if (tab.value === 'image') {
                await runImageTask();
            } else {
                await runVideoTask();
            }
        };

        const resolveImageApiKey = (model) => {
            const keyByModel = {
                'gpt-image-2': config.gptImage2ApiKey,
                'gemini-3-pro-image-preview': config.gemini3ProImageApiKey,
                'gemini-3.1-flash-image-preview': config.gemini31FlashImageApiKey,
            };
            return keyByModel[model] || config.defaultApiKey || '';
        };

        const resolveVideoApiKey = () => config.videoApiKey || config.defaultApiKey || '';

        const appendCommonImageFields = (formData, source, apiKey) => {
            formData.append('base_url', config.baseUrl);
            formData.append('proxy_url', config.proxyUrl || '');
            formData.append('api_key', apiKey || '');
            formData.append('model', source.model);
            formData.append('prompt', source.prompt);
            formData.append('aspect_ratio', source.aspectRatio);
            source.imageUrls.split('\n').map(s => s.trim()).filter(Boolean).forEach(url => {
                formData.append('image_url', url);
            });
        };

        const onImageFilesChange = (event) => {
            imageFiles.value = Array.from(event.target.files || []);
        };

        const onVideoFilesChange = (event) => {
            videoFiles.value = Array.from(event.target.files || []);
        };

        const createPlaceholder = (item) => {
            const result = {
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                taskId: null,
                status: 'starting',
                url: null,
                error: null,
                logs: [],
                createdAt: new Date().toLocaleString(),
                ...item,
            };
            results.value.unshift(result);
            currentResult.value = result;
            currentLogs.value = result.logs;
            return result;
        };

        const updateResult = (id, patch) => {
            const item = results.value.find(result => result.id === id);
            if (!item) return null;
            Object.assign(item, patch);
            if (currentResult.value?.id === id) {
                currentResult.value = item;
                currentLogs.value = item.logs || [];
                scrollLogs();
            }
            return item;
        };

        const openPreview = (item) => {
            currentResult.value = item;
            currentLogs.value = item.logs || [];
            if (item.status === 'completed' && item.url) {
                selectedResult.value = item;
            }
            scrollLogs();
        };

        const closePreview = () => {
            selectedResult.value = null;
        };

        const clearResults = () => {
            results.value = [];
            currentResult.value = null;
            selectedResult.value = null;
            currentLogs.value = [];
            pushLog(`[System] Cleared workspace results.`);
        };

        const runImageTask = async () => {
            const meta = image.model === 'gpt-image-2'
                ? `${image.size} · ${image.aspectRatio} · ${image.quality || 'medium'}`
                : `${image.size} · ${image.aspectRatio}`;
            const placeholder = createPlaceholder({
                type: 'image',
                model: image.model,
                prompt: image.prompt,
                meta,
                logs: [
                    `[System] Image task queued.`,
                    `[Config] Model: ${image.model} | ${meta}`,
                ],
            });
            if (imageFiles.value.length) {
                placeholder.logs.push(`[Input] ${imageFiles.value.length} local reference image(s) attached.`);
            }
            currentLogs.value = placeholder.logs;

            try {
                const payload = new FormData();
                appendCommonImageFields(payload, image, resolveImageApiKey(image.model));
                payload.append('image_size', image.size);
                if (image.model === 'gpt-image-2') {
                    payload.append('quality', image.quality || 'medium');
                }
                imageFiles.value.forEach(file => payload.append('image_file', file));

                const res = await fetch('/api/image', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || 'Failed to start image task');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[System] Backend accepted task ${data.internal_task_id}.`],
                });
                pollTask(data.internal_task_id, placeholder.id);
            } catch (err) {
                updateResult(placeholder.id, {
                    status: 'error',
                    error: err.message,
                    logs: [...placeholder.logs, `[Error] ${err.message}`],
                });
                console.error(err);
            } finally {
                isSubmitting.value = false;
            }
        };

        const runVideoTask = async () => {
            const meta = `${video.duration}s · ${video.aspectRatio}`;
            const placeholder = createPlaceholder({
                type: 'video',
                model: video.model,
                prompt: video.prompt,
                meta,
                logs: [
                    `[System] Video task queued.`,
                    `[Config] Model: ${video.model} | ${meta}`,
                ],
            });
            if (videoFiles.value.length) {
                placeholder.logs.push(`[Input] ${videoFiles.value.length} local reference image(s) attached.`);
            }
            currentLogs.value = placeholder.logs;

            try {
                const payload = new FormData();
                appendCommonImageFields(payload, video, resolveVideoApiKey());
                payload.append('duration', video.duration);
                payload.append('create_path', video.createPath);
                payload.append('status_path', video.statusPath);
                videoFiles.value.forEach(file => payload.append('image_file', file));

                const res = await fetch('/api/video', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || 'Failed to start video task');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[System] Backend accepted task ${data.internal_task_id}.`],
                });
                pollTask(data.internal_task_id, placeholder.id);
            } catch (err) {
                updateResult(placeholder.id, {
                    status: 'error',
                    error: err.message,
                    logs: [...placeholder.logs, `[Error] ${err.message}`],
                });
                console.error(err);
            } finally {
                isSubmitting.value = false;
            }
        };

        const pollTask = (taskId, resultId) => {
            const pollInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/task/${taskId}`);
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.message || 'Task status request failed');

                    const item = results.value.find(result => result.id === resultId);
                    if (!item) {
                        clearInterval(pollInterval);
                        return;
                    }

                    const logs = data.logs?.length ? data.logs : item.logs;
                    const patch = {
                        status: data.status || item.status,
                        logs,
                        error: data.error || item.error,
                    };

                    if (data.status === 'completed') {
                        patch.url = data.asset?.url || (item.type === 'image' ? data.image_url : data.media_url);
                        patch.filename = data.asset?.filename || item.filename;
                        patch.remote_url = data.asset?.remote_url || data.remote_url || item.remote_url;
                        patch.createdAt = data.asset?.createdAt || item.createdAt;
                    }

                    updateResult(resultId, patch);

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                        clearInterval(pollInterval);
                        const finalItem = results.value.find(result => result.id === resultId);
                        if (finalItem && finalItem.status === 'completed') {
                            finalItem.logs = [...(finalItem.logs || []), `[System] ${finalItem.type === 'image' ? 'Image' : 'Video'} ready.`];
                        }
                        if (finalItem && finalItem.status !== 'completed' && !finalItem.error) {
                            finalItem.error = 'Task failed.';
                        }
                        if (currentResult.value?.id === resultId) {
                            currentLogs.value = finalItem?.logs || [];
                            scrollLogs();
                        }
                    }
                } catch (e) {
                    console.error('Poll error', e);
                }
            }, 2000);
        };

        return {
            tab, isLoading, isSubmitting,
            config, image, video,
            imageFiles, videoFiles,
            onImageFilesChange, onVideoFilesChange,
            results, selectedResult,
            openPreview, closePreview, clearResults,
            submitTask,
            currentLogs, currentResult
        };
    }
});

app.mount('#app');
