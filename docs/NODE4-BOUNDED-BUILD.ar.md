# طابور بناء NODE4 المغلق

على الهوية الدقيقة NODE4 فقط، يذهب Run Remote Builder إلى bounded-build بدل default.
عمال Supervisor الحاليون لا يستمعون إليه؛ غياب العامل المقيد يترك الطلب منتظرًا، ولا يحوله لمسار غير مقيد.
لا تغيير للطوابير أو العمال أو الإعدادات في NODE1/2/3، ولا إعادة توجيه لعمليات backup أو بقية Agent jobs.
لا يُضاف عامل دائم أو صلاحية socket أو sudo. عامل البناء المدار المؤقت يحتاج وحدة MemoryMax3G/swap0/CPU0-6/nice19
ومحرك Docker/containerd خاصًا تحت نفس السقف، وطلب fin/remote.sh ثابتًا، والتحقق من مهمة/صورة/SHAs محددة.
مسار نشر هذا المصدر هو Server.update_agent/Agent.update المعتاد بعد PR ومراجعة Sol واحدة وحفظ آخر-good caf04f5/config.
هذا التغيير وحده لا يبني صورة ولا يثبت العامل المقيد؛ لا يبدأ Hub build حتى نشر العامل وأدلته وبوابة الاسترجاع.
اختبارات policy نقية بلا Redis/DB؛ يلزم بعد النشر التحقق من head وqueue policy والعمال الحاليين دون ادعاء native build PASS.
