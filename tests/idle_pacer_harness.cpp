#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>

class CGameSpeed {
public:
    bool IsActuallyPaused() const;
};
static bool paused_flag = true;
bool CGameSpeed::IsActuallyPaused() const { return paused_flag; }

class CInGameIdler {
public:
    virtual void KeyFunction();
    char padding[0x400]{};
    CGameSpeed *speed = nullptr;
};
void CInGameIdler::KeyFunction() {}

class CApplication {
public:
    char padding[0x40]{};
    CInGameIdler *current = nullptr;
    static CApplication *AccessInstance();
};
static CApplication app;
CApplication *CApplication::AccessInstance() { return &app; }

static bool set_mode(int mode) {
    FILE *file=std::fopen(std::getenv("EU4_PACE_CONTROL"),"r+b");
    if (!file) return false;
    std::fputc(mode,file);
    std::fclose(file);
    return true;
}

int main() {
    CGameSpeed speed;
    CInGameIdler idler;
    idler.speed = &speed;
    app.current = &idler;
    if ((char *)&idler.speed-(char *)&idler != 0x408 ||
        (char *)&app.current-(char *)&app != 0x40) return 7;
    CGLPixelFormatAttribute attributes[] = {kCGLPFAAccelerated,
                                              static_cast<CGLPixelFormatAttribute>(0)};
    CGLPixelFormatObj format = nullptr;
    CGLContextObj context = nullptr;
    GLint formats = 0;
    if (CGLChoosePixelFormat(attributes,&format,&formats) != kCGLNoError || !format) return 2;
    if (CGLCreateContext(format,nullptr,&context) != kCGLNoError || !context) return 3;
    CGLSetCurrentContext(context);
    for (int i=0;i<660;i++) {
        CGLFlushDrawable(context);
        usleep(10000);
        if (i==110 && !set_mode(1)) return 4;
        if (i==220) {
            paused_flag=false;
            if (!set_mode(0)) return 4;
        }
        if (i==330 && !set_mode(1)) return 4;
        if (i==440) {
            paused_flag=true;
            setenv("EU4_PACE_TEST_IDLE","0",1);
        }
        if (i==550) {
            app.current=nullptr;
            setenv("EU4_PACE_TEST_IDLE","1",1);
        }
    }
    CGLSetCurrentContext(nullptr);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    std::puts("harness complete");
    return 0;
}
