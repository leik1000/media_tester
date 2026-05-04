const { createApp, ref, reactive, watch, onMounted, computed } = Vue;

const app = createApp({
    setup() {
        const tab = ref('image');
        const isSubmitting = ref(false);
        const currentLogs = ref([]);
        const currentResult = ref(null);
        const results = ref([]);
        const selectedResult = ref(null);
        const showLogs = ref(false);
        const currentPage = ref(1);
        const pageSize = 25;
        const imageFiles = ref([]);
        const videoFiles = ref([]);

        const isLoading = computed(() =>
            results.value.some(item => item.status === 'starting' || item.status === 'running')
        );
        const totalPages = computed(() => Math.max(1, Math.ceil(results.value.length / pageSize)));
        const paginatedResults = computed(() => {
            const start = (currentPage.value - 1) * pageSize;
            return results.value.slice(start, start + pageSize);
        });

        const config = reactive({
            baseUrl: 'https://api.pixellelabs.com',
            enableProxy: true,
            proxyUrl: 'http://127.0.0.1:10808',
            defaultApiKey: '',
            gptImage2ApiKey: '',
            gemini3ProImageApiKey: '',
            gemini31FlashImageApiKey: '',
            videoApiKey: ''
        });

        const image = reactive({
            model: 'gemini-3-pro-image-preview',
            prompt: '电影感山间日出，云雾缓慢流动',
            aspectRatio: '16:9',
            size: '2K',
            quality: 'medium',
            imageUrls: ''
        });

        const video = reactive({
            model: 'sora2',
            prompt: '电影感蜂鸟飞过阳光花园',
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
                    console.error('加载配置失败', e);
                }
            }
            await loadPersistedAssets();
        });


        const loadPersistedAssets = async () => {
            try {
                const res = await fetch('/api/assets');
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '加载历史资产失败');
                results.value = (data.assets || []).map(item => ({
                    logs: [`[系统] 已从 downloads/${item.filename} 加载。`],
                    ...item,
                }));
                currentPage.value = 1;
                if (results.value.length) {
                    currentResult.value = results.value[0];
                    currentLogs.value = currentResult.value.logs || [];
                }
            } catch (e) {
                console.error('加载历史资产失败', e);
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
            formData.append('proxy_url', config.enableProxy ? (config.proxyUrl || '') : '');
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

        const appendReferenceUrl = (target, url) => {
            const value = String(url || '').trim();
            if (!value) return;
            const current = target.imageUrls
                .split('\n')
                .map(item => item.trim())
                .filter(Boolean);
            if (!current.includes(value)) {
                current.push(value);
            }
            target.imageUrls = current.join('\n');
        };

        const onResultDragStart = (event, item) => {
            if (item.status !== 'completed' || !item.url) return;
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('text/plain', item.url);
            event.dataTransfer.setData('application/x-media-tester-url', item.url);
        };

        const onReferenceDrop = (event, targetName) => {
            const url = event.dataTransfer.getData('application/x-media-tester-url') || event.dataTransfer.getData('text/plain');
            appendReferenceUrl(targetName === 'video' ? video : image, url);
            pushLog(`[输入] 已从画廊添加参考图：${url}`);
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
            currentPage.value = 1;
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

        const toggleLogs = () => {
            showLogs.value = !showLogs.value;
            if (showLogs.value) scrollLogs();
        };

        const closeLogs = () => {
            showLogs.value = false;
        };

        const clearResults = () => {
            results.value = [];
            currentResult.value = null;
            selectedResult.value = null;
            currentLogs.value = [];
            pushLog(`[系统] 已清空当前画廊显示。`);
        };

        const nextPage = () => {
            currentPage.value = Math.min(totalPages.value, currentPage.value + 1);
        };

        const prevPage = () => {
            currentPage.value = Math.max(1, currentPage.value - 1);
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
                    `[系统] 图片任务已加入队列。`,
                    `[配置] 模型：${image.model} | ${meta}`,
                ],
            });
            if (imageFiles.value.length) {
                placeholder.logs.push(`[输入] 已附加 ${imageFiles.value.length} 张本地参考图。`);
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
                if (!res.ok) throw new Error(data.message || '图片任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
            } catch (err) {
                updateResult(placeholder.id, {
                    status: 'error',
                    error: err.message,
                    logs: [...placeholder.logs, `[错误] ${err.message}`],
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
                    `[系统] 视频任务已加入队列。`,
                    `[配置] 模型：${video.model} | ${meta}`,
                ],
            });
            if (videoFiles.value.length) {
                placeholder.logs.push(`[输入] 已附加 ${videoFiles.value.length} 张本地参考图。`);
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
                if (!res.ok) throw new Error(data.message || '视频任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
            } catch (err) {
                updateResult(placeholder.id, {
                    status: 'error',
                    error: err.message,
                    logs: [...placeholder.logs, `[错误] ${err.message}`],
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
                    if (!res.ok) throw new Error(data.message || '任务状态查询失败');

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
                            finalItem.logs = [...(finalItem.logs || []), `[系统] ${finalItem.type === 'image' ? '图片' : '视频'}已完成。`];
                        }
                        if (finalItem && finalItem.status !== 'completed' && !finalItem.error) {
                            finalItem.error = '任务失败。';
                        }
                        if (currentResult.value?.id === resultId) {
                            currentLogs.value = finalItem?.logs || [];
                            scrollLogs();
                        }
                    }
                } catch (e) {
                    console.error('轮询失败', e);
                }
            }, 2000);
        };

        return {
            tab, isLoading, isSubmitting,
            config, image, video,
            imageFiles, videoFiles,
            onImageFilesChange, onVideoFilesChange,
            onResultDragStart, onReferenceDrop,
            results, paginatedResults, currentPage, totalPages, selectedResult, showLogs,
            openPreview, closePreview, toggleLogs, closeLogs, clearResults,
            nextPage, prevPage,
            submitTask,
            currentLogs, currentResult
        };
    }
});

app.mount('#app');
