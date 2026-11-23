/* Host-only FFmpeg boundary declarations, not an encoder implementation. */
#ifndef UVC_STARTUP_FFMPEG_H
#define UVC_STARTUP_FFMPEG_H
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
#define AVERROR(e) (-(e))
#define AVERROR_EOF (-541478725)
#define AVMEDIA_TYPE_VIDEO 0
#define AV_PIX_FMT_NV12 1
#define AV_CODEC_FLAG_LOW_DELAY 1
#define AV_CODEC_FLAG_LOOP_FILTER 2
#define AVFMT_FLAG_NONBLOCK 4
#define AVFMT_FLAG_NOBUFFER 8
#define AVFMT_FLAG_IGNDTS 16
typedef struct { int num, den; } AVRational;
typedef struct { const char *fflags; } AVDictionary;
typedef struct { int unused; } AVInputFormat;
typedef struct { int id; } AVCodec;
typedef struct {
    uint8_t *data;
    int size, stream_index;
    int64_t pts;
} AVPacket;
typedef struct {
    int width, height, format, codec_id, profile, level;
    long bit_rate;
} AVCodecParameters;
typedef struct { AVCodecParameters *codecpar; } AVStream;
typedef struct {
    int (*callback)(void *);
    void *opaque;
} AVIOInterruptCB;
typedef struct {
    AVStream **streams;
    unsigned int nb_streams;
    int flags;
    AVIOInterruptCB interrupt_callback;
} AVFormatContext;
typedef struct {
    int codec_id, codec_type, width, height, pix_fmt;
    AVRational time_base;
    long bit_rate;
    int gop_size, max_b_frames, qmin, qmax;
    double qcompress;
    int rc_min_rate, rc_max_rate, rc_buffer_size, rc_initial_buffer_occupancy;
    int refs, me_range, max_qdiff, flags, mb_decision, trellis;
} AVCodecContext;
typedef struct {
    int format, width, height;
    uint8_t *data[8];
    int linesize[8];
    int64_t pts;
} AVFrame;
void avdevice_register_all(void);
AVInputFormat *av_find_input_format(const char *);
int av_dict_set(AVDictionary **, const char *, const char *, int);
int av_dict_set_int(AVDictionary **, const char *, int64_t, int);
void av_dict_free(AVDictionary **);
AVFormatContext *avformat_alloc_context(void);
int avformat_open_input(AVFormatContext **, const char *, AVInputFormat *, AVDictionary **);
int avformat_find_stream_info(AVFormatContext *, void *);
void avformat_close_input(AVFormatContext **);
const AVCodec *avcodec_find_encoder_by_name(const char *);
AVCodecContext *avcodec_alloc_context3(const AVCodec *);
int avcodec_open2(AVCodecContext *, const AVCodec *, AVDictionary **);
void avcodec_free_context(AVCodecContext **);
AVFrame *av_frame_alloc(void);
int av_frame_get_buffer(AVFrame *, int);
void av_frame_free(AVFrame **);
AVPacket *av_packet_alloc(void);
void av_packet_free(AVPacket **);
void av_packet_unref(AVPacket *);
void av_packet_move_ref(AVPacket *, AVPacket *);
int av_read_frame(AVFormatContext *, AVPacket *);
int avcodec_send_frame(AVCodecContext *, AVFrame *);
int avcodec_receive_packet(AVCodecContext *, AVPacket *);
#endif
