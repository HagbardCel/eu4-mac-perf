/* Synthetic default-drawable format observer (issue #278).
 *
 * Creates a small legacy-CGL context, records the effective pixel-format
 * attributes and the default framebuffer's attachment component identity, then
 * destroys the context.  No EU4 process is involved.  Values the driver cannot
 * expose are recorded as "unavailable:<reason>"; they are never defaulted from
 * the requested attributes. */

#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#import <OpenGL/gl3.h>
#include <stdio.h>
#include <stdlib.h>

static const char *error_text(GLenum error) {
    switch (error) {
        case GL_NO_ERROR: return "none";
        case GL_INVALID_ENUM: return "invalid_enum";
        case GL_INVALID_VALUE: return "invalid_value";
        case GL_INVALID_OPERATION: return "invalid_operation";
        default: return "gl_error";
    }
}

static void drain_errors(void) {
    for (int index = 0; index < 16 && glGetError() != GL_NO_ERROR; index++) {
    }
}

static void print_cgl_value(CGLPixelFormatObj format, CGLPixelFormatAttribute attribute,
                            const char *name, int *first) {
    GLint value = 0;
    CGLError error = CGLDescribePixelFormat(format, 0, attribute, &value);
    printf("%s\"%s\":%s", *first ? "" : ",", name,
           error == kCGLNoError ? "" : "");
    if (error == kCGLNoError) {
        printf("%d", (int)value);
    } else {
        printf("\"unavailable:cgl_error_%d\"", (int)error);
    }
    *first = 0;
}

static void print_cgl_profile(CGLPixelFormatObj format, int *first) {
    GLint value = 0;
    CGLError error = CGLDescribePixelFormat(
        format, 0, kCGLPFAOpenGLProfile, &value);
    printf("%s\"profile\":", *first ? "" : ",");
    if (error == kCGLNoError) {
        if (value == kCGLOGLPVersion_3_2_Core) {
            printf("\"3.2_core\"");
        } else if (value == kCGLOGLPVersion_Legacy) {
            printf("\"legacy\"");
        } else {
            printf("\"0x%x\"", (unsigned)value);
        }
    } else {
        printf("\"unavailable:cgl_error_%d\"", (int)error);
    }
    *first = 0;
}

static void print_attachment(GLenum attachment, const char *label, int *first) {
    GLint type = 0;
    drain_errors();
    glGetFramebufferAttachmentParameteriv(
        GL_FRAMEBUFFER, attachment,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &type);
    GLenum type_error = glGetError();
    printf("%s\"%s\":{\"object_type\":", *first ? "" : ",", label);
    if (type_error != GL_NO_ERROR) {
        printf("\"unavailable:%s\"", error_text(type_error));
    } else if (type == GL_NONE) {
        printf("\"absent\"");
    } else if (type == GL_FRAMEBUFFER_DEFAULT) {
        printf("\"framebuffer_default\"");
    } else if (type == GL_TEXTURE) {
        printf("\"texture\"");
    } else if (type == GL_RENDERBUFFER) {
        printf("\"renderbuffer\"");
    } else {
        printf("\"0x%x\"", (unsigned)type);
    }
    const struct {
        GLenum parameter;
        const char *name;
    } fields[] = {
        {GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE, "component_type"},
        {GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE, "red_size"},
        {GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE, "green_size"},
        {GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE, "blue_size"},
        {GL_FRAMEBUFFER_ATTACHMENT_ALPHA_SIZE, "alpha_size"},
    };
    for (size_t index = 0;
         index < sizeof(fields) / sizeof(fields[0]); index++) {
        GLint value = 0;
        drain_errors();
        glGetFramebufferAttachmentParameteriv(
            GL_FRAMEBUFFER, attachment, fields[index].parameter, &value);
        GLenum error = glGetError();
        printf(",\"%s\":", fields[index].name);
        if (error != GL_NO_ERROR) {
            printf("\"unavailable:%s\"", error_text(error));
        } else if (fields[index].parameter ==
                   GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE) {
            switch ((GLenum)value) {
                case GL_FLOAT: printf("\"float\""); break;
                case GL_INT: printf("\"int\""); break;
                case GL_UNSIGNED_INT: printf("\"unsigned_int\""); break;
                case GL_SIGNED_NORMALIZED: printf("\"signed_normalized\""); break;
                case GL_UNSIGNED_NORMALIZED:
                    printf("\"unsigned_normalized\""); break;
                default: printf("\"0x%x\"", (unsigned)value); break;
            }
        } else {
            printf("%d", (int)value);
        }
    }
    printf("}");
    *first = 0;
}

int main(void) {
    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAAccelerated,
        kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        kCGLPFADepthSize, (CGLPixelFormatAttribute)24,
        kCGLPFAStencilSize, (CGLPixelFormatAttribute)8,
        kCGLPFADoubleBuffer,
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        (CGLPixelFormatAttribute)0,
    };
    CGLPixelFormatObj pixel_format = NULL;
    GLint format_count = 0;
    CGLError error = CGLChoosePixelFormat(attributes, &pixel_format, &format_count);
    if (error != kCGLNoError || pixel_format == NULL) {
        printf("{\"schema\":\"gfx-drawable-observation/v1\","
               "\"status\":\"unavailable\",\"reason\":\"choose_pixel_format_%d\"}\n",
               (int)error);
        return 1;
    }
    CGLContextObj context = NULL;
    error = CGLCreateContext(pixel_format, NULL, &context);
    if (error != kCGLNoError || context == NULL) {
        CGLDestroyPixelFormat(pixel_format);
        printf("{\"schema\":\"gfx-drawable-observation/v1\","
               "\"status\":\"unavailable\",\"reason\":\"create_context_%d\"}\n",
               (int)error);
        return 1;
    }
    CGLContextObj previous = CGLGetCurrentContext();
    if (CGLSetCurrentContext(context) != kCGLNoError) {
        CGLDestroyContext(context);
        CGLDestroyPixelFormat(pixel_format);
        printf("{\"schema\":\"gfx-drawable-observation/v1\","
               "\"status\":\"unavailable\",\"reason\":\"set_current_context\"}\n");
        return 1;
    }
    printf("{\"schema\":\"gfx-drawable-observation/v1\",\"status\":\"ok\","
           "\"context\":{\"format_count\":%d,\"cgl\":{", (int)format_count);
    int first = 1;
    print_cgl_value(pixel_format, kCGLPFAColorSize, "color_size", &first);
    print_cgl_value(pixel_format, kCGLPFAAlphaSize, "alpha_size", &first);
    print_cgl_value(pixel_format, kCGLPFADepthSize, "depth_size", &first);
    print_cgl_value(pixel_format, kCGLPFAStencilSize, "stencil_size", &first);
    print_cgl_value(pixel_format, kCGLPFASampleBuffers, "sample_buffers", &first);
    print_cgl_value(pixel_format, kCGLPFASamples, "samples", &first);
    print_cgl_value(pixel_format, kCGLPFAAccelerated, "accelerated", &first);
    print_cgl_value(pixel_format, kCGLPFADoubleBuffer, "double_buffer", &first);
    print_cgl_value(pixel_format, kCGLPFAStereo, "stereo", &first);
    print_cgl_value(pixel_format, kCGLPFARendererID, "renderer_id", &first);
    print_cgl_profile(pixel_format, &first);
    printf("},\"renderer\":{\"vendor\":\"%s\",\"renderer\":\"%s\","
           "\"version\":\"%s\"},\"default_framebuffer\":{",
           (const char *)glGetString(GL_VENDOR),
           (const char *)glGetString(GL_RENDERER),
           (const char *)glGetString(GL_VERSION));
    GLint draw_binding = 0, samples = 0, sample_buffers = 0;
    GLint draw_buffer = 0, read_buffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_binding);
    glGetIntegerv(GL_SAMPLES, &samples);
    glGetIntegerv(GL_SAMPLE_BUFFERS, &sample_buffers);
    glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    printf("\"draw_framebuffer_binding\":%d,\"samples\":%d,"
           "\"sample_buffers\":%d,\"draw_buffer\":\"0x%x\",\"read_buffer\":\"0x%x\",",
           (int)draw_binding, (int)samples, (int)sample_buffers,
           (unsigned)draw_buffer, (unsigned)read_buffer);
    first = 1;
    print_attachment(GL_BACK_LEFT, "color_back_left", &first);
    printf(",");
    first = 1;
    print_attachment(GL_DEPTH, "depth", &first);
    printf(",");
    first = 1;
    print_attachment(GL_STENCIL, "stencil", &first);
    printf("}},\"render_target_path\":\"%s\"}\n",
           draw_binding == 0 ? "default-window" : "fbo");
    CGLSetCurrentContext(previous);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(pixel_format);
    return 0;
}
