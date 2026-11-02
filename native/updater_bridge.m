// Thin Qt/Python bridge; Sparkle performs downloads, signature verification and installation.
#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>

typedef int (*BVStateCallback)(int stage);
static BVStateCallback stateCallback;
static SPUStandardUpdaterController *controller;
static NSString *lastError;

@interface BVUpdaterDelegate : NSObject <SPUUpdaterDelegate>
@end
@implementation BVUpdaterDelegate
- (BOOL)updater:(SPUUpdater *)updater shouldPostponeRelaunchForUpdate:(SUAppcastItem *)item untilInvokingBlock:(void (^)(void))installHandler {
    // Never quit until the Python controller has atomically saved its current state.
    dispatch_async(dispatch_get_main_queue(), ^{
        if (stateCallback && stateCallback(1)) {
            installHandler();
        } else {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"进度尚未保存，更新已暂缓";
            alert.informativeText = @"请检查存档目录的空间和访问权限。当前应用和存档均保留。保存成功后，请重新启动应用再检查更新。";
            [alert addButtonWithTitle:@"知道了"];
            [alert runModal];
        }
    });
    return YES;
}
- (void)updaterWillRelaunchApplication:(SPUUpdater *)updater {
    if (stateCallback) stateCallback(2);
}
@end
static BVUpdaterDelegate *delegate;

int BVUpdaterStart(BVStateCallback callback) {
    @autoreleasepool {
        if (![NSThread isMainThread]) { lastError = @"更新必须从界面主线程启动。"; return 0; }
        if (controller) return 1;
        stateCallback = callback;
        delegate = [[BVUpdaterDelegate alloc] init];
        controller = [[SPUStandardUpdaterController alloc] initWithStartingUpdater:NO updaterDelegate:delegate userDriverDelegate:nil];
        NSError *error = nil;
        if (![controller.updater startUpdater:&error]) {
            lastError = error.localizedDescription ?: @"更新组件暂时无法启动。";
            controller = nil;
            return 0;
        }
        return 1;
    }
}
int BVUpdaterCheck(void) {
    @autoreleasepool {
        if (![NSThread isMainThread] || !controller) return 0;
        if (!controller.updater.canCheckForUpdates) return 2;
        [controller checkForUpdates:nil];
        return 1;
    }
}
const char *BVUpdaterError(void) {
    return (lastError ?: @"更新组件暂时无法启动。").UTF8String;
}
