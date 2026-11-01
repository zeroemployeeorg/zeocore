bump: patch
Test only: the two timeout cases of the notebook descendant-cleanup test now allow seconds, not one or two, for the kernel to start and write its pid files before a timeout fires. On a busy machine the 2 s whole-run case failed 4 of 20 full gates because the pid file was not there yet. No runtime, workflow or dependency change.
