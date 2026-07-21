const { createApp, ref, reactive, watch, onMounted, computed } = Vue;

const rawFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
    const res = await rawFetch(...args);
    if (res.status === 401 && window.location.pathname !== '/login') {
        window.location.href = '/login';
    }
    return res;
};

const VIDEO_MODEL_CAPS = {
    'kling-video-3.0': {
        ratios: ['1:1', '16:9', '9:16'],
        resolutions: ['720p', '1080p'],
        durationRange: [3, 15],
        maxRefs: 1,
        supportsStartEnd: false,
        supportsVideoReference: false,
    },
    'kling-video-o3-omni': {
        ratios: ['1:1', '16:9', '9:16'],
        resolutions: ['720p', '1080p'],
        durationRange: [3, 15],
        maxRefs: 7,
        supportsStartEnd: true,
        supportsVideoReference: true,
    },
    'sora2': {
        ratios: ['16:9', '9:16'],
        resolutions: ['720p'],
        durations: [4, 8, 12],
        maxRefs: 1,
        supportsStartEnd: false,
        supportsVideoReference: false,
    },
    'sora-v3-pro': {
        ratios: ['21:9', '1:1', '4:3', '3:4', '16:9', '9:16'],
        resolutions: ['480p', '720p'],
        durationRange: [4, 15],
        maxRefs: 4,
        supportsStartEnd: true,
        supportsVideoReference: true,
    },
    'sora-v3-fast': {
        ratios: ['21:9', '1:1', '4:3', '3:4', '16:9', '9:16'],
        resolutions: ['480p', '720p'],
        durationRange: [4, 15],
        maxRefs: 4,
        supportsStartEnd: true,
        supportsVideoReference: true,
    },
    'veo31-fast': {
        ratios: ['16:9', '9:16'],
        resolutions: ['720p', '1080p'],
        durations: [4, 6, 8],
        maxRefs: 1,
        supportsStartEnd: false,
        supportsVideoReference: false,
    },
};

const BASE_IMAGE_RATIOS = ['1:1', '4:3', '3:4', '5:4', '4:5', '3:2', '2:3', '16:9', '9:16', '21:9'];
const IMAGE_MODEL_CAPS = {
    'gemini-3-pro-image-preview': {
        ratios: ['auto', ...BASE_IMAGE_RATIOS],
    },
    'gemini-3.1-flash-image-preview': {
        ratios: ['auto', ...BASE_IMAGE_RATIOS, '1:4', '4:1', '1:8', '8:1'],
    },
    'gpt-image-2': {
        ratios: BASE_IMAGE_RATIOS,
    },
};

const app = createApp({
    setup() {
        const tab = ref('image');
        const isSubmitting = ref(false);
        const currentLogs = ref([]);
        const currentResult = ref(null);
        const results = ref([]);
        const selectedResult = ref(null);
        const previewImageScale = ref(1);
        const previewImageTransformOrigin = ref('center center');
        const showLogs = ref(false);
        const showSystemConfig = ref(false);
        const configLoaded = ref(false);
        const currentPage = ref(1);
        const totalItems = ref(0);
        const totalPages = ref(1);
        const galleryFilter = ref('all');
        const pageSize = 25;
        let saveConfigTimer = null;
        const activePolls = new Map();
        const imageFiles = ref([]);
        const videoFiles = ref([]);

        const isLoading = computed(() =>
            results.value.some(item => item.status === 'starting' || item.status === 'running')
        );
        const paginatedResults = computed(() => results.value);

        const countReferenceUrls = (value) => String(value || '')
            .split('\n')
            .map(item => item.trim())
            .filter(Boolean)
            .length;
        const imageReferenceCount = computed(() => countReferenceUrls(image.imageUrls) + imageFiles.value.length);
        const videoReferenceCount = computed(() => countReferenceUrls(video.imageUrls) + videoFiles.value.length);
        const previewImageStyle = computed(() => ({
            transform: `scale(${previewImageScale.value})`,
            transformOrigin: previewImageTransformOrigin.value,
        }));

        const config = reactive({
            baseUrl: 'https://api.pixellelabs.com',
            enableProxy: true,
            proxyUrl: 'http://127.0.0.1:10808',
            gptImage2ApiKey: '',
            gemini3ProImageApiKey: '',
            gemini31FlashImageApiKey: '',
            videoApiKeys: Object.fromEntries(Object.keys(VIDEO_MODEL_CAPS).map(model => [model, '']))
        });

        const authSettings = reactive({
            username: 'admin',
            newPassword: '',
            message: '',
        });

        const systemSettings = reactive({
            saving: false,
            message: '',
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
            resolution: '720p',
            duration: 4,
            createPath: '/v1/videos',
            statusPath: '/v1/videos/{task_id}',
            startFrame: '',
            endFrame: '',
            videoReference: '',
            imageUrls: ''
        });

        const videoModelOptions = Object.keys(VIDEO_MODEL_CAPS);
        const currentImageCapability = computed(() => IMAGE_MODEL_CAPS[image.model] || IMAGE_MODEL_CAPS['gemini-3-pro-image-preview']);
        const imageAspectRatios = computed(() => currentImageCapability.value.ratios);
        const currentVideoCapability = computed(() => VIDEO_MODEL_CAPS[video.model] || VIDEO_MODEL_CAPS.sora2);
        const videoDurationOptions = computed(() => currentVideoCapability.value.durations || []);
        const videoDurationRange = computed(() => currentVideoCapability.value.durationRange || null);
        const videoDurationMin = computed(() => videoDurationRange.value ? videoDurationRange.value[0] : null);
        const videoDurationMax = computed(() => videoDurationRange.value ? videoDurationRange.value[1] : null);

        const normalizeImageSettings = () => {
            if (!IMAGE_MODEL_CAPS[image.model]) image.model = 'gemini-3-pro-image-preview';
            const cap = currentImageCapability.value;
            if (!cap.ratios.includes(image.aspectRatio)) image.aspectRatio = cap.ratios[0];
        };

        const normalizeVideoSettings = () => {
            if (!VIDEO_MODEL_CAPS[video.model]) video.model = 'sora2';
            const cap = currentVideoCapability.value;
            if (!cap.ratios.includes(video.aspectRatio)) video.aspectRatio = cap.ratios[0];
            if (!cap.resolutions.includes(video.resolution)) video.resolution = cap.resolutions[0];
            if (cap.durations) {
                const value = Number(video.duration);
                if (!cap.durations.includes(value)) video.duration = cap.durations[0];
            } else if (cap.durationRange) {
                const [min, max] = cap.durationRange;
                const value = Number(video.duration) || min;
                video.duration = Math.min(max, Math.max(min, value));
            }
            if (!cap.supportsStartEnd) {
                video.startFrame = '';
                video.endFrame = '';
            }
            if (!cap.supportsVideoReference) {
                video.videoReference = '';
            }
        };

        onMounted(async () => {
            const authenticated = await loadAuthStatus();
            if (!authenticated) return;
            await loadSavedConfig();
            configLoaded.value = true;
            await loadPersistedTasks();
        });

        const loadAuthStatus = async () => {
            try {
                const res = await fetch('/api/auth/status');
                const data = await res.json();
                if (!res.ok || !data.authenticated) {
                    window.location.href = '/login';
                    return false;
                }
                if (data.username) {
                    authSettings.username = data.username;
                }
                return true;
            } catch (e) {
                window.location.href = '/login';
                return false;
            }
        };

        const logout = async () => {
            await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
            activePolls.forEach(interval => clearInterval(interval));
            activePolls.clear();
            configLoaded.value = false;
            showSystemConfig.value = false;
            results.value = [];
            currentResult.value = null;
            currentLogs.value = [];
            window.location.href = '/login';
        };

        const updateAuth = async () => {
            authSettings.message = '';
            const res = await fetch('/api/auth/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: authSettings.username,
                    password: authSettings.newPassword,
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.message || '保存登录配置失败');
            authSettings.username = data.username || authSettings.username;
            authSettings.newPassword = '';
            authSettings.message = '登录配置已保存。';
            return data;
        };

        const openSystemConfig = () => {
            systemSettings.message = '';
            showSystemConfig.value = true;
        };

        const closeSystemConfig = () => {
            showSystemConfig.value = false;
        };

        const serializeConfig = () => ({
            baseUrl: config.baseUrl,
            enableProxy: config.enableProxy,
            proxyUrl: config.proxyUrl,
            gptImage2ApiKey: config.gptImage2ApiKey,
            gemini3ProImageApiKey: config.gemini3ProImageApiKey,
            gemini31FlashImageApiKey: config.gemini31FlashImageApiKey,
            videoApiKeys: { ...config.videoApiKeys },
        });

        const saveCurrentConfig = async () => {
            clearTimeout(saveConfigTimer);
            const payload = JSON.stringify({ config: serializeConfig(), image, video });
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.message || '保存配置失败');
            }
        };

        const saveSystemConfig = async () => {
            systemSettings.saving = true;
            systemSettings.message = '';
            try {
                await saveCurrentConfig();
                await updateAuth();
                systemSettings.message = '系统配置已保存。';
            } catch (e) {
                systemSettings.message = e.message || '保存系统配置失败';
            } finally {
                systemSettings.saving = false;
            }
        };

        const applySavedConfig = (savedData) => {
            const savedConfig = savedData.config || {};
            ['baseUrl', 'enableProxy', 'proxyUrl', 'gptImage2ApiKey', 'gemini3ProImageApiKey', 'gemini31FlashImageApiKey'].forEach(key => {
                if (Object.prototype.hasOwnProperty.call(savedConfig, key)) {
                    config[key] = savedConfig[key];
                }
            });
            if (savedConfig.videoApiKeys && typeof savedConfig.videoApiKeys === 'object' && !Array.isArray(savedConfig.videoApiKeys)) {
                Object.keys(VIDEO_MODEL_CAPS).forEach(model => {
                    if (Object.prototype.hasOwnProperty.call(savedConfig.videoApiKeys, model)) {
                        config.videoApiKeys[model] = savedConfig.videoApiKeys[model] || '';
                    }
                });
            }
            Object.assign(image, savedData.image || {});
            Object.assign(video, savedData.video || {});
            normalizeImageSettings();
            normalizeVideoSettings();
        };

        const loadSavedConfig = async () => {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                if (res.ok && data.config && Object.keys(data.config).length) {
                    applySavedConfig(data.config);
                }
            } catch (e) {
                console.error('读取数据库配置失败', e);
            }
        };


        const loadPersistedTasks = async (page = currentPage.value) => {
            try {
                const params = new URLSearchParams({
                    page: String(page),
                    page_size: String(pageSize),
                    type: galleryFilter.value,
                });
                const res = await fetch(`/api/tasks?${params.toString()}`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '加载任务失败');
                results.value = (data.tasks || []).map(item => ({
                    id: item.id || item.internal_task_id,
                    taskId: item.taskId || item.internal_task_id || item.id,
                    url: item.url || item.image_url || item.media_url || item.local_url,
                    thumbnailUrl: item.thumbnailUrl || item.thumbnail_url || null,
                    logs: item.logs?.length ? item.logs : [`[系统] 已从数据库加载任务。`],
                    durationSeconds: item.durationSeconds ?? item.duration_seconds ?? null,
                    ...item,
                }));
                currentPage.value = data.page || page;
                totalItems.value = data.total || 0;
                totalPages.value = data.total_pages || 1;
                if (results.value.length) {
                    currentResult.value = results.value[0];
                    currentLogs.value = currentResult.value.logs || [];
                } else {
                    currentResult.value = null;
                    currentLogs.value = [];
                }
                results.value
                    .filter(item => (item.status === 'pending' || item.status === 'running') && item.taskId)
                    .forEach(item => pollTask(item.taskId, item.id));
            } catch (e) {
                console.error('加载任务失败', e);
            }
        };

        const refreshTaskList = async () => {
            await loadPersistedTasks(currentPage.value);
        };

        const setGalleryFilter = async (filter) => {
            galleryFilter.value = filter;
            currentPage.value = 1;
            await loadPersistedTasks(1);
        };

        watch([config, image, video], () => {
            if (!configLoaded.value) return;
            clearTimeout(saveConfigTimer);
            saveConfigTimer = setTimeout(() => {
                saveCurrentConfig().catch(e => console.error('保存数据库配置失败', e));
            }, 400);
        }, { deep: true });

        watch(() => video.model, normalizeVideoSettings);
        watch(() => image.model, normalizeImageSettings);

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
            return keyByModel[model] || '';
        };

        const resolveVideoApiKey = (model) => config.videoApiKeys[model] || '';

        const appendCommonTaskFields = (formData, source, apiKey, options = {}) => {
            formData.append('base_url', config.baseUrl);
            formData.append('proxy_url', config.enableProxy ? (config.proxyUrl || '') : '');
            formData.append('api_key', apiKey || '');
            formData.append('model', source.model);
            formData.append('prompt', source.prompt);
            formData.append('aspect_ratio', source.aspectRatio);
            if (options.includeReferences !== false) {
                source.imageUrls.split('\n').map(s => s.trim()).filter(Boolean).forEach(url => {
                    formData.append('image_url', url);
                });
            }
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
            const startedAtMs = Date.now();
            const result = {
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                taskId: null,
                status: 'starting',
                url: null,
                error: null,
                logs: [],
                createdAt: new Date().toLocaleString(),
                startedAtMs,
                finishedAtMs: null,
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
            previewImageScale.value = 1;
            previewImageTransformOrigin.value = 'center center';
            if ((item.status === 'completed' && item.url) || item.status === 'error' || item.status === 'failed') {
                selectedResult.value = item;
            }
            scrollLogs();
        };

        const onPreviewImageWheel = (event) => {
            if (selectedResult.value?.type !== 'image') return;
            const imageEl = event.currentTarget.querySelector('img');
            if (imageEl) {
                const rect = imageEl.getBoundingClientRect();
                const x = rect.width ? ((event.clientX - rect.left) / rect.width) * 100 : 50;
                const y = rect.height ? ((event.clientY - rect.top) / rect.height) * 100 : 50;
                previewImageTransformOrigin.value = `${Math.min(100, Math.max(0, x))}% ${Math.min(100, Math.max(0, y))}%`;
            }
            const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
            const nextScale = previewImageScale.value * factor;
            previewImageScale.value = Math.round(Math.min(5, Math.max(0.5, nextScale)) * 100) / 100;
        };

        const formatJson = (value) => {
            if (!value) return '';
            try {
                return JSON.stringify(value, null, 2);
            } catch (_) {
                return String(value);
            }
        };

        const formatTaskDuration = (item) => {
            if (!item || item.status !== 'completed') return '';
            if (item.durationSeconds !== null && item.durationSeconds !== undefined) {
                return `${Number(item.durationSeconds) || 0}s`;
            }
            if (!item.startedAtMs || !item.finishedAtMs) return '';
            const seconds = Math.max(0, Math.round((item.finishedAtMs - item.startedAtMs) / 1000));
            return `${seconds}s`;
        };

        const closePreview = () => {
            selectedResult.value = null;
            previewImageScale.value = 1;
            previewImageTransformOrigin.value = 'center center';
        };

        const toggleLogs = () => {
            showLogs.value = !showLogs.value;
            if (showLogs.value) scrollLogs();
        };

        const closeLogs = () => {
            showLogs.value = false;
        };

        const nextPage = async () => {
            const next = Math.min(totalPages.value, currentPage.value + 1);
            if (next !== currentPage.value) await loadPersistedTasks(next);
        };

        const prevPage = async () => {
            const prev = Math.max(1, currentPage.value - 1);
            if (prev !== currentPage.value) await loadPersistedTasks(prev);
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
                appendCommonTaskFields(payload, image, resolveImageApiKey(image.model));
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
                if (galleryFilter.value !== 'all' && galleryFilter.value !== 'image') {
                    await loadPersistedTasks(currentPage.value);
                }
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
            normalizeVideoSettings();
            const cap = currentVideoCapability.value;
            const usingStartEnd = cap.supportsStartEnd && (String(video.startFrame || '').trim() || String(video.endFrame || '').trim());
            const meta = `${video.duration}s · ${video.aspectRatio} · ${video.resolution}`;
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
                if (!usingStartEnd) {
                    const urlRefCount = video.imageUrls.split('\n').map(s => s.trim()).filter(Boolean).length;
                    const referenceCount = urlRefCount + videoFiles.value.length;
                    if (referenceCount > cap.maxRefs) {
                        throw new Error(`当前模型最多支持 ${cap.maxRefs} 张参考图。`);
                    }
                }
                const payload = new FormData();
                appendCommonTaskFields(payload, video, resolveVideoApiKey(video.model), { includeReferences: !usingStartEnd });
                payload.append('duration', video.duration);
                payload.append('resolution', video.resolution);
                payload.append('create_path', video.createPath);
                payload.append('status_path', video.statusPath);
                if (cap.supportsStartEnd) {
                    if (video.startFrame.trim()) payload.append('start_frame', video.startFrame.trim());
                    if (video.endFrame.trim()) payload.append('end_frame', video.endFrame.trim());
                }
                if (cap.supportsVideoReference && video.videoReference.trim()) {
                    payload.append('video_reference', video.videoReference.trim());
                }
                if (!usingStartEnd) {
                    videoFiles.value.forEach(file => payload.append('image_file', file));
                }

                const res = await fetch('/api/video', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '视频任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
                if (galleryFilter.value !== 'all' && galleryFilter.value !== 'video') {
                    await loadPersistedTasks(currentPage.value);
                }
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
            if (activePolls.has(taskId)) return;
            const pollInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/task/${taskId}`);
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.message || '任务状态查询失败');

                    const item = results.value.find(result => result.id === resultId);
                    if (!item) {
                        clearInterval(pollInterval);
                        activePolls.delete(taskId);
                        return;
                    }

                    const logs = data.logs?.length ? data.logs : item.logs;
                    const patch = {
                        status: data.status || item.status,
                        logs,
                        error: data.error || item.error,
                        apiTaskId: data.api_task_id || item.apiTaskId,
                        requestPayload: data.request_payload || item.requestPayload,
                        raw: data.raw || item.raw,
                        thumbnailUrl: data.thumbnailUrl || data.thumbnail_url || data.asset?.thumbnailUrl || data.asset?.thumbnail_url || item.thumbnailUrl,
                        thumbnail_url: data.thumbnail_url || data.thumbnailUrl || data.asset?.thumbnail_url || data.asset?.thumbnailUrl || item.thumbnail_url,
                        durationSeconds: data.durationSeconds ?? data.duration_seconds ?? item.durationSeconds,
                    };

                    if (data.status === 'completed') {
                        patch.url = data.asset?.url || (item.type === 'image' ? data.image_url : data.media_url);
                        patch.filename = data.asset?.filename || item.filename;
                        patch.remote_url = data.asset?.remote_url || data.remote_url || item.remote_url;
                        patch.createdAt = data.asset?.createdAt || item.createdAt;
                    }

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                        patch.finishedAtMs = item.finishedAtMs || Date.now();
                    }

                    updateResult(resultId, patch);

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                        clearInterval(pollInterval);
                        activePolls.delete(taskId);
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
            activePolls.set(taskId, pollInterval);
        };

        return {
            tab, isLoading, isSubmitting,
            authSettings, systemSettings,
            config, image, video,
            videoModelOptions, currentImageCapability, imageAspectRatios, currentVideoCapability, videoDurationOptions, videoDurationRange, videoDurationMin, videoDurationMax,
            imageFiles, videoFiles, imageReferenceCount, videoReferenceCount,
            onImageFilesChange, onVideoFilesChange,
            onResultDragStart, onReferenceDrop,
            results, paginatedResults, currentPage, totalPages, totalItems, galleryFilter, selectedResult, previewImageStyle, showLogs, showSystemConfig,
            openPreview, closePreview, toggleLogs, closeLogs, openSystemConfig, closeSystemConfig, saveSystemConfig, refreshTaskList, setGalleryFilter,
            onPreviewImageWheel,
            logout, updateAuth,
            nextPage, prevPage,
            submitTask,
            currentLogs, currentResult, formatJson, formatTaskDuration
        };
    }
});

app.mount('#app');
