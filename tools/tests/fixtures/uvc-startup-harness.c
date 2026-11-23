/* Run the actual package C, replacing only encoder/camera and USB ioctls. */
#include <assert.h>
#include <stdatomic.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/eventfd.h>
#include "uvc-startup-ffmpeg.h"
#undef _DEFAULT_SOURCE
#include "lib/events.c"
#include "lib/video-buffers.c"
#include "lib/video-source.c"
#include "lib/vpu-source.c"
#include "lib/stream.c"

static pthread_t main_thread;
static struct v4l2_device sink;
static struct events event_loop;
static struct uvc_stream *stream;
static struct video_source *source;
static int qbuf_count, empty_count, stream_on_count, stream_off_count;
static int queue_error, stream_error, complete_index = -1;
static unsigned int lengths[128];
static int owned[2];
static atomic_int packet_objects;
static pthread_mutex_t camera_mutex = PTHREAD_MUTEX_INITIALIZER;
static int camera_sizes[128], camera_head, camera_tail;
static _Thread_local int encoded_size, encoded_ready;
static atomic_int reads, produced, eagain_reads;
static int send_error, receive_wedged;
static atomic_int receive_entered;

static void on_main(void) { assert(pthread_equal(pthread_self(), main_thread)); }
static long long milliseconds(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec * 1000LL + now.tv_nsec / 1000000;
}
static void provide_frame(int size)
{
    pthread_mutex_lock(&camera_mutex);
    assert(camera_tail < 128);
    camera_sizes[camera_tail++] = size;
    pthread_mutex_unlock(&camera_mutex);
}

void avdevice_register_all(void) {}
AVInputFormat *av_find_input_format(const char *name)
{ static AVInputFormat format; return &format; }
int av_dict_set(AVDictionary **dict, const char *key, const char *value, int flags)
{
    if (!strcmp(key, "fflags")) {
        if (!*dict) *dict = calloc(1, sizeof(**dict));
        (*dict)->fflags = value;
    }
    return 0;
}
int av_dict_set_int(AVDictionary **dict, const char *key, int64_t value, int flags)
{ return 0; }
void av_dict_free(AVDictionary **dict) { free(*dict); *dict = NULL; }
AVFormatContext *avformat_alloc_context(void) { return calloc(1, sizeof(AVFormatContext)); }
int avformat_open_input(AVFormatContext **ctx, const char *device,
                        AVInputFormat *format, AVDictionary **options)
{
    if (!*ctx) *ctx = avformat_alloc_context();
#if !LEGACY_STARTUP
    assert(!pthread_equal(pthread_self(), main_thread));
    /* FFmpeg applies dictionary flags before calling the demuxer header.
     * An unprefixed fflags value replaces flags set directly on context. */
    if (*options && (*options)->fflags && (*options)->fflags[0] != '+')
        (*ctx)->flags = 0;
    assert((*ctx)->flags & AVFMT_FLAG_NONBLOCK);
#endif
    (*ctx)->nb_streams = 1;
    (*ctx)->streams = calloc(1, sizeof(AVStream *));
    (*ctx)->streams[0] = calloc(1, sizeof(AVStream));
    AVCodecParameters *params = calloc(1, sizeof(*params));
    params->width = params->height = 8;
    params->format = AV_PIX_FMT_NV12;
    (*ctx)->streams[0]->codecpar = params;
    return 0;
}
int avformat_find_stream_info(AVFormatContext *ctx, void *options) { return 0; }
void avformat_close_input(AVFormatContext **ctx)
{
    if (!*ctx) return;
    free((*ctx)->streams[0]->codecpar);
    free((*ctx)->streams[0]); free((*ctx)->streams); free(*ctx); *ctx = NULL;
}
const AVCodec *avcodec_find_encoder_by_name(const char *name)
{ static AVCodec codec; return &codec; }
AVCodecContext *avcodec_alloc_context3(const AVCodec *codec)
{ return calloc(1, sizeof(AVCodecContext)); }
int avcodec_open2(AVCodecContext *ctx, const AVCodec *codec, AVDictionary **options)
{
#if !LEGACY_STARTUP
    assert(!pthread_equal(pthread_self(), main_thread));
#endif
    return 0;
}
void avcodec_free_context(AVCodecContext **ctx) { free(*ctx); *ctx = NULL; }
AVFrame *av_frame_alloc(void) { return calloc(1, sizeof(AVFrame)); }
int av_frame_get_buffer(AVFrame *frame, int alignment) { return 0; }
void av_frame_free(AVFrame **frame) { free(*frame); *frame = NULL; }
AVPacket *av_packet_alloc(void)
{
    atomic_fetch_add(&packet_objects, 1);
    return calloc(1, sizeof(AVPacket));
}
void av_packet_free(AVPacket **packet)
{
    if (!*packet) return;
    free((*packet)->data); free(*packet); *packet = NULL;
    atomic_fetch_sub(&packet_objects, 1);
}
void av_packet_move_ref(AVPacket *dest, AVPacket *packet)
{ *dest = *packet; memset(packet, 0, sizeof(*packet)); }
void av_packet_unref(AVPacket *packet)
{ free(packet->data); memset(packet, 0, sizeof(*packet)); }
int av_read_frame(AVFormatContext *ctx, AVPacket *packet)
{
    for (;;) {
#if LEGACY_STARTUP
        if (ctx->interrupt_callback.callback &&
            ctx->interrupt_callback.callback(ctx->interrupt_callback.opaque))
            return -EINTR;
#endif
        pthread_mutex_lock(&camera_mutex);
        if (camera_head < camera_tail) {
            encoded_size = camera_sizes[camera_head++];
            pthread_mutex_unlock(&camera_mutex);
            packet->size = 96;
            packet->data = calloc(1, 96);
            atomic_fetch_add(&reads, 1);
            return 0;
        }
        pthread_mutex_unlock(&camera_mutex);
#if !LEGACY_STARTUP
        /* Match the vendor V4L2 demuxer: nonblocking DQBUF immediately
         * reports EAGAIN while capture has no completed frame. */
        atomic_fetch_add(&eagain_reads, 1);
        return -EAGAIN;
#else
        usleep(1000);
#endif
    }
}
int avcodec_send_frame(AVCodecContext *ctx, AVFrame *frame)
{
    if (send_error) return -EIO;
    encoded_ready = 1;
    return 0;
}
int avcodec_receive_packet(AVCodecContext *ctx, AVPacket *packet)
{
    if (!encoded_ready) return -EAGAIN;
    if (receive_wedged) {
        /* Vendor encoder poll(-1) can ignore camera nonblock and stop flags. */
        atomic_store(&receive_entered, 1);
        for (;;) usleep(10000);
    }
    encoded_ready = 0;
    packet->size = encoded_size;
    packet->data = malloc(encoded_size > 0 ? encoded_size : 1);
    memset(packet->data, 0x5a, encoded_size > 0 ? encoded_size : 1);
    if (encoded_size >= 4) {
        packet->data[0] = 0xff; packet->data[1] = 0xd8;
        packet->data[encoded_size - 2] = 0xff;
        packet->data[encoded_size - 1] = 0xd9;
    }
    packet->pts = 1234567;
    atomic_fetch_add(&produced, 1);
    return 0;
}

struct v4l2_device *uvc_v4l2_device(struct uvc_device *uvc) { return &sink; }
struct uvc_device *uvc_open(const char *name, struct uvc_stream *s)
{ return (struct uvc_device *)&sink; }
void uvc_close(struct uvc_device *uvc) {}
void uvc_set_config(struct uvc_device *uvc, struct uvc_function_config *fc) {}
void uvc_events_init(struct uvc_device *uvc, struct events *events) {}
int uvc_set_format(struct uvc_device *uvc, struct v4l2_pix_format *format) { return 0; }
int v4l2_alloc_buffers(struct v4l2_device *dev, enum v4l2_memory mem, unsigned int count)
{
    on_main(); assert(count == 2); assert(!dev->buffers.nbufs);
    dev->buffers.nbufs = count;
    dev->buffers.buffers = calloc(count, sizeof(struct video_buffer));
    for (unsigned int i = 0; i < count; i++) {
        dev->buffers.buffers[i].index = i;
        dev->buffers.buffers[i].size = 128;
        dev->buffers.buffers[i].mem = calloc(1, 128);
    }
    return 0;
}
int v4l2_free_buffers(struct v4l2_device *dev)
{
    on_main();
    for (unsigned int i = 0; i < dev->buffers.nbufs; i++) free(dev->buffers.buffers[i].mem);
    free(dev->buffers.buffers); memset(&dev->buffers, 0, sizeof(dev->buffers));
    memset(owned, 0, sizeof(owned)); complete_index = -1;
    return 0;
}
int v4l2_mmap_buffers(struct v4l2_device *dev) { on_main(); return 0; }
int v4l2_import_buffers(struct v4l2_device *dev, const struct video_buffer_set *buffers)
{ assert(0); return -EINVAL; }
int v4l2_queue_buffer(struct v4l2_device *dev, struct video_buffer *buf)
{
    on_main();
    assert(buf->index < 2); assert(!owned[buf->index]);
    if (queue_error) return -EIO;
    owned[buf->index] = 1;
    lengths[qbuf_count++] = buf->bytesused;
    if (!buf->bytesused) empty_count++;
#if !LEGACY_STARTUP
    assert(buf->bytesused > 0 && buf->bytesused <= buf->size);
    unsigned char *jpeg = buf->mem;
    assert(jpeg[0] == 0xff && jpeg[1] == 0xd8);
    assert(jpeg[buf->bytesused - 2] == 0xff && jpeg[buf->bytesused - 1] == 0xd9);
    for (unsigned int i = 2; i + 2 < buf->bytesused; i++) assert(jpeg[i] == 0x5a);
    assert(buf->timestamp.tv_sec == 1 && buf->timestamp.tv_usec == 234567);
#endif
    return 0;
}
int v4l2_dequeue_buffer(struct v4l2_device *dev, struct video_buffer *buf)
{
    on_main();
    if (complete_index < 0) return -EAGAIN;
    int index = complete_index; complete_index = -1;
    assert(owned[index]); owned[index] = 0;
    *buf = dev->buffers.buffers[index];
    return 0;
}
int v4l2_stream_on(struct v4l2_device *dev)
{
    on_main(); stream_on_count++;
    assert(qbuf_count >= 2);
    return stream_error ? -EIO : 0;
}
int v4l2_stream_off(struct v4l2_device *dev)
{ on_main(); stream_off_count++; return 0; }

/* Use production events_dispatch with real source/timer readability. V4L2
 * writable readiness is controlled by the mocked device completion state. */
static void pump_once(void)
{
    fd_set readfds = event_loop.rfds, writefds = event_loop.wfds;
    fd_set exceptfds = event_loop.efds;
    if (complete_index < 0) FD_CLR(sink.fd, &writefds);
    struct timeval timeout = { .tv_usec = 2000 };
    int result = select(event_loop.maxfd + 1, &readfds, &writefds, &exceptfds, &timeout);
    assert(result >= 0);
    if (result) events_dispatch(&event_loop, &readfds, &writefds, &exceptfds);
}
static void pump_for(int duration)
{
    long long until = milliseconds() + duration;
    do { pump_once(); } while (milliseconds() < until && !event_loop.done);
}
static void wait_qbuf(int expected)
{
    long long until = milliseconds() + 1000;
    while (qbuf_count < expected && !event_loop.done && milliseconds() < until) pump_once();
    assert(qbuf_count == expected);
}
static void wait_produced(int expected)
{
    long long until = milliseconds() + 1000;
    while (atomic_load(&produced) < expected && milliseconds() < until) usleep(1000);
    assert(atomic_load(&produced) >= expected);
}
static void setup(void)
{
    main_thread = pthread_self();
    sink.fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC); assert(sink.fd >= 0);
    events_init(&event_loop);
    source = vpu_source_create("host-camera"); assert(source);
    vpu_source_init(source, &event_loop);
    stream = uvc_stream_new("host-usb"); assert(stream);
    uvc_stream_set_event_handler(stream, &event_loop);
    uvc_stream_set_video_source(stream, source);
    struct v4l2_pix_format format = { .width = 8, .height = 8,
                                    .pixelformat = V4L2_PIX_FMT_MJPEG };
    assert(uvc_stream_set_format(stream, &format) == 0);
}
static void teardown(void)
{
    uvc_stream_enable(stream, 0);
    uvc_stream_delete(stream);
    video_source_destroy(source);
    assert(!sink.buffers.nbufs);
    assert(atomic_load(&packet_objects) == 0);
    assert(list_empty(&event_loop.events));
    events_cleanup(&event_loop); close(sink.fd);
}

int main(int argc, char **argv)
{
    assert(argc == 2); setup();
#if LEGACY_STARTUP
    /* Keep the legacy worker's FFmpeg boundary interruptible for teardown;
     * the QBUF regression itself runs entirely unchanged production code. */
    struct vpu_source *vpu = to_vpu_source(source);
    uvc_stream_enable(stream, 1);
    assert(qbuf_count == 2 && empty_count == 2 && stream_on_count == 1);
    vpu->fmt_ctx->interrupt_callback.callback = NULL;
    /* Old stream_off cannot interrupt read_frame; one host frame releases it. */
    vpu->stop_flag = 1; provide_frame(32);
    teardown();
#else
    const char *scenario = argv[1];
    if (!strcmp(scenario, "queue-error")) queue_error = 1;
    if (!strcmp(scenario, "stream-error")) stream_error = 1;
    if (!strcmp(scenario, "encoder-error")) send_error = 1;
    if (!strcmp(scenario, "wedged-encoder")) receive_wedged = 1;
    uvc_stream_enable(stream, 1);
    assert(qbuf_count == 0 && stream_on_count == 0);
    struct vpu_source *vpu = to_vpu_source(source);
    if (!strcmp(scenario, "wedged-encoder")) {
        provide_frame(32);
        long long until = milliseconds() + 1000;
        while (!atomic_load(&receive_entered) && milliseconds() < until) usleep(1000);
        assert(atomic_load(&receive_entered));
        /* Production must fail the process within the join deadline. */
        uvc_stream_enable(stream, 0);
        assert(0);
    } else if (!strcmp(scenario, "delayed")) {
        pump_for(30); assert(qbuf_count == 0);
        assert(atomic_load(&eagain_reads) > 0);
        /* An early wake has no packet and must not consume pending buffers. */
        uint64_t wake = 1;
        assert(write(vpu->event_fd, &wake, sizeof(wake)) == sizeof(wake));
        pump_for(10); assert(qbuf_count == 0);
        provide_frame(31); provide_frame(47);
        wait_produced(2); /* Multiple packets can coalesce into one wake. */
        wait_qbuf(2); assert(lengths[0] == 31 && lengths[1] == 47);
        assert(stream_on_count == 1);
        complete_index = 0; pump_for(10);
        assert(qbuf_count == 2); /* No replacement JPEG: hold this buffer. */
        provide_frame(63); wait_qbuf(3); assert(lengths[2] == 63);
        assert(stream_on_count == 1);
    } else if (!strcmp(scenario, "oversize") || !strcmp(scenario, "zero")) {
        provide_frame(!strcmp(scenario, "zero") ? 0 : 129);
        pump_for(100); assert(event_loop.done && qbuf_count == 0);
    } else if (!strcmp(scenario, "encoder-error")) {
        provide_frame(32); pump_for(100);
        assert(event_loop.done && qbuf_count == 0);
    } else if (!strcmp(scenario, "queue-error") || !strcmp(scenario, "stream-error")) {
        provide_frame(31); provide_frame(47); pump_for(100);
        assert(event_loop.done);
        assert(qbuf_count == (queue_error ? 0 : 2));
    } else if (!strcmp(scenario, "timeout")) {
        /* Arm the production startup timer for 10ms instead of waiting 10s. */
        struct itimerspec timeout = { .it_value.tv_nsec = 10000000 };
        assert(timerfd_settime(stream->startup_fd, 0, &timeout, NULL) == 0);
        pump_for(100); assert(event_loop.done && qbuf_count == 0);
    } else if (!strcmp(scenario, "stop")) {
        pump_for(10); assert(atomic_load(&eagain_reads) > 0);
        long long start = milliseconds();
        uvc_stream_enable(stream, 0);
        assert(milliseconds() - start < 500);
        pump_for(10); assert(!event_loop.done && qbuf_count == 0);
        /* A partially primed startup must release the first queued JPEG too. */
        uvc_stream_enable(stream, 1);
        provide_frame(31); wait_qbuf(1);
        assert(stream_on_count == 0);
        uvc_stream_enable(stream, 0);
        assert(!sink.buffers.nbufs && !vpu->pending_buffers);
    } else if (!strcmp(scenario, "cycles")) {
        for (int cycle = 0; cycle < 4; cycle++) {
            if (cycle) {
                uvc_stream_enable(stream, 1);
            }
            /* Leave the main loop idle: two queued packets fill the bounded
             * ring and the third producer blocks until stream_off wakes it. */
            int expected = atomic_load(&produced) + 3;
            provide_frame(31); provide_frame(47); provide_frame(63);
            wait_produced(expected);
            long long start = milliseconds();
            uvc_stream_enable(stream, 0);
            assert(milliseconds() - start < 500);
            assert(!vpu->thread_started && !vpu->packet_queue && !vpu->pending_buffers);
            assert(atomic_load(&packet_objects) == 0);
            pump_for(10); assert(!event_loop.done && qbuf_count == 0);
        }
    } else if (!strcmp(scenario, "restart")) {
        for (int cycle = 0; cycle < 3; cycle++) {
            if (cycle) uvc_stream_enable(stream, 1);
            assert(qbuf_count == cycle * 2);
            /* Stale or duplicate readiness without a packet is harmless. */
            uint64_t wake = 7;
            assert(write(vpu->event_fd, &wake, sizeof(wake)) == sizeof(wake));
            pump_for(5); assert(qbuf_count == cycle * 2);
            provide_frame(31); wait_qbuf(cycle * 2 + 1);
            assert(stream_on_count == cycle);
            provide_frame(47); wait_qbuf(cycle * 2 + 2);
            assert(stream_on_count == cycle + 1);
            uvc_stream_enable(stream, 0);
            assert(atomic_load(&packet_objects) == 0);
            /* Leave source readiness and timer expiration pending at stop,
             * then verify production stop/start drains both descriptors. */
            assert(write(vpu->event_fd, &wake, sizeof(wake)) == sizeof(wake));
            struct itimerspec expired = { .it_value.tv_nsec = 1 };
            assert(timerfd_settime(stream->startup_fd, 0, &expired, NULL) == 0);
            usleep(1000);
            uvc_stream_enable(stream, 0);
            pump_for(5); assert(!event_loop.done);
        }
    } else assert(0);
    assert(empty_count == 0);
    teardown();
#endif
    return 0;
}
