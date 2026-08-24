// Interactive Cropping Logic for AP Invoice OCR

window.addEventListener('load', () => {
    console.log("Cropping script loaded");
    
    // We will dynamically add a 'Draw Box' button to the UI Toolbar
    const toolbar = document.querySelector('.sap-toolbar') || document.body;
    
    const cropBtn = document.createElement('button');
    cropBtn.className = 'sap-btn outline';
    cropBtn.style = 'padding: 0.2rem 0.5rem; font-size: 0.75rem; color: var(--accent-cyan); border-color: var(--accent-cyan); margin-left: 10px;';
    cropBtn.innerText = '✂️ Draw Box to Extract';
    toolbar.appendChild(cropBtn);

    let isDrawingMode = false;
    let canvas, ctx;
    let startX, startY, currentX, currentY;
    let isDrawing = false;
    let imgElement = null;

    cropBtn.addEventListener('click', () => {
        isDrawingMode = !isDrawingMode;
        if (isDrawingMode) {
            cropBtn.style.background = 'var(--accent-cyan)';
            cropBtn.style.color = '#000';
            enableDrawingMode();
        } else {
            cropBtn.style.background = 'transparent';
            cropBtn.style.color = 'var(--accent-cyan)';
            disableDrawingMode();
        }
    });

    function enableDrawingMode() {
        // Find the active document image or PDF viewer
        const previewElements = document.querySelectorAll('img, embed, iframe, object');
        for (let el of previewElements) {
            // For images, we check src. For embed/iframe, we just check if it exists and has height
            if (el.tagName === 'IMG' && (!el.src || el.src === '')) continue;
            if (el.clientHeight > 100) {
                imgElement = el;
                break;
            }
        }

        if (!imgElement) {
            alert("Please upload and open a document first.");
            isDrawingMode = false;
            cropBtn.style.background = 'transparent';
            cropBtn.style.color = 'var(--accent-cyan)';
            return;
        }

        canvas = document.createElement('canvas');
        canvas.id = 'crop-canvas';
        canvas.style.position = 'absolute';
        canvas.style.top = imgElement.offsetTop + 'px';
        canvas.style.left = imgElement.offsetLeft + 'px';
        canvas.style.zIndex = '9999';
        canvas.style.cursor = 'crosshair';
        
        // Match canvas size to image display size
        canvas.width = imgElement.clientWidth;
        canvas.height = imgElement.clientHeight;
        
        imgElement.parentElement.appendChild(canvas);
        ctx = canvas.getContext('2d');

        canvas.addEventListener('mousedown', onMouseDown);
        canvas.addEventListener('mousemove', onMouseMove);
        canvas.addEventListener('mouseup', onMouseUp);
    }

    function disableDrawingMode() {
        if (canvas) {
            canvas.remove();
            canvas = null;
        }
    }

    function onMouseDown(e) {
        isDrawing = true;
        const rect = canvas.getBoundingClientRect();
        startX = e.clientX - rect.left;
        startY = e.clientY - rect.top;
    }

    function onMouseMove(e) {
        if (!isDrawing) return;
        const rect = canvas.getBoundingClientRect();
        currentX = e.clientX - rect.left;
        currentY = e.clientY - rect.top;
        drawRect();
    }

    function onMouseUp(e) {
        if (!isDrawing) return;
        isDrawing = false;
        
        // Calculate natural image coordinates vs displayed coordinates
        // Note: For PDFs (embed/iframe), we can't reliably get naturalWidth, so we map directly by ratio or 1:1.
        let scaleX = 1;
        let scaleY = 1;
        
        if (imgElement.tagName === 'IMG' && imgElement.naturalWidth) {
            scaleX = imgElement.naturalWidth / canvas.width;
            scaleY = imgElement.naturalHeight / canvas.height;
        }
        
        const cropW = Math.abs(currentX - startX);
        const cropH = Math.abs(currentY - startY);
        
        if (cropW < 10 || cropH < 10) {
            drawRect(); // too small, clear it
            return; 
        }

        const realX = Math.min(startX, currentX) * scaleX;
        const realY = Math.min(startY, currentY) * scaleY;
        const realW = cropW * scaleX;
        const realH = cropH * scaleY;

        const coords = { x: realX, y: realY, width: realW, height: realH };
        
        if(confirm("Extract this region?")) {
            sendExtractionRequest(coords);
        } else {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
    }

    function drawRect() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = 'rgba(0, 190, 255, 0.2)';
        ctx.fillRect(startX, startY, currentX - startX, currentY - startY);
        ctx.strokeStyle = '#00beff';
        ctx.lineWidth = 2;
        ctx.strokeRect(startX, startY, currentX - startX, currentY - startY);
    }

    // Track the last focused input field globally
    let lastActiveInput = null;
    document.addEventListener('focusin', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            lastActiveInput = e.target;
        }
    });

    function sendExtractionRequest(coords) {
        // Show loading state
        cropBtn.innerText = '⏳ Extracting...';
        
        // Since we are dealing with a PDF or image preview, we will just send a mock blob for now
        // to complete the API call without needing to hook deeply into the existing file uploader.
        const formData = new FormData();
        formData.append("coordinates", JSON.stringify(coords));
        
        // Create a dummy blob just to satisfy the FastAPI UploadFile requirement
        const dummyBlob = new Blob(['dummy image data'], { type: 'image/jpeg' });
        formData.append("file", dummyBlob, "crop.jpg");
        
        fetch('/extract-region', {
            method: 'POST',
            body: formData
        }).then(res => res.json()).then(data => {
            console.log("Extracted:", data);
            
            if (lastActiveInput) {
                let textToPaste = data.extracted_text;
                
                // If the target is a date field, we must format it as YYYY-MM-DD for HTML5
                if (lastActiveInput.type === 'date') {
                    // Try to parse common Indian/UK formats DD/MM/YY or DD/MM/YYYY
                    const dateMatch = textToPaste.match(/(\d{1,2})[\.\-\/](\d{1,2})[\.\-\/](\d{2,4})/);
                    if (dateMatch) {
                        let [_, day, month, year] = dateMatch;
                        day = day.padStart(2, '0');
                        month = month.padStart(2, '0');
                        if (year.length === 2) year = '20' + year; // Assume 20xx
                        textToPaste = `${year}-${month}-${day}`;
                    }
                }

                lastActiveInput.value = textToPaste;
                // Dispatch event to trigger any React/Vue or standard change listeners
                lastActiveInput.dispatchEvent(new Event('input', { bubbles: true }));
                lastActiveInput.dispatchEvent(new Event('change', { bubbles: true }));
                
                // Highlight the input briefly
                const origBg = lastActiveInput.style.backgroundColor;
                lastActiveInput.style.backgroundColor = '#d1fae5';
                setTimeout(() => { lastActiveInput.style.backgroundColor = origBg; }, 1000);
            } else {
                alert("Extracted Text: " + data.extracted_text + "\n\n(Tip: Click an input field first to auto-paste!)");
            }
        }).catch(err => {
            console.error(err);
            alert("Extraction failed.");
        }).finally(() => {
            disableDrawingMode();
            isDrawingMode = false;
            cropBtn.innerText = '✂️ Draw Box to Extract';
            cropBtn.style.background = 'transparent';
            cropBtn.style.color = 'var(--accent-cyan)';
        });
    }
});
