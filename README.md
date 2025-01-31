# Flux 1.1 Pro functionality for generating images in Open WebUI

## Overview

This Python function integrates the **Black Forest Labs FLUX** image generation API into the Open WebUI platform. It supports several FLUX models:

- `flux-dev`
- `flux-pro-1.1`
- `flux-pro-1.1-ultra`

With this function, users can generate high-quality images, customize parameters like resolution, aspect ratio, and output format, and handle result polling robustly.

## Features

- **Model Support**: Provides support for FLUX models, including high-resolution `flux-pro-1.1-ultra`.
  
- **Customizable Parameters**: Allows setting image width, height, aspect ratio, safety tolerance, and output format.
  
- **RAW Image Option**: Enables less processed, natural-looking images for supported models.
  
- **Result Polling**: Automatically polls the API until the result is ready.
  
- **Saving the result**: Save generated images to the default directory on the server `{CACHE_DIR}+(image/generations/)`. This way, generated images are displayed in the dialog from the local directory, not from the Black Forest Labs cloud link.
  
- **Error Handling**: Includes detailed error handling for API failures, timeouts, and input validation errors.
  
- **Prompt Translation and Normalization**: Automatically translates and optimizes user prompts using the OpenAI API, ensuring prompts are structured, concise, and tailored for optimal image generation results.

- **Standardized Dimension Parameter**: Introduces a unified `dimension` parameter that combines model selection with resolution or aspect ratio settings. This standardization replaces the previous method of separately specifying model and size parameters, making the configuration more intuitive and user-friendly.

## Requirements

**Python Libraries**:

- `typing`
- `pydantic`
- `requests`

## Usage

### Function Integration

Place the plugin code in the Open WebUI feature catalog manually or use the blue Get button at [openwebui.com](https://openwebui.com/f/fovendor/flux_1_1_pro_function).

### Parameter Entry

The following parameters are customizable through the `Valves` class:

| Parameter           | Type  | Default                         | Description                                                                                                    |
| ------------------- | ----- | ------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `BFL_API_KEY`       | `str` | `""`                            | API key for Black Forest Labs.                                                                                |
| `api_base_url`      | `str` | `"https://api.bfl.ml/v1"`        | Base URL for the FLUX API.                                                                                     |
| `dimension`         | `str` | `"flux-dev: 1440x1440"`          | Combined parameter for selecting the model and its resolution or aspect ratio. Previously, specifying model and size was unclear and inconvenient. Now, with `dimension`, selection is standardized and highly convenient using predefined options. |
| `raw`               | `bool` | `False`                         | Generate less processed images (only available for `flux-pro-1.1-ultra`).                                       |
| `safety_tolerance` | `int`  | `2`                             | Tolerance level for moderation (0 = strict, 6 = lenient).                                                    |
| `output_format`    | `str`  | `"jpeg"`                        | Format of the output image (`jpeg` or `png`).                                                                  |

#### Dimension Options

The `dimension` parameter leverages `DIMENSION_OPTIONS` to provide a unified and convenient selection of model-specific settings. This eliminates the need to manually input separate parameters for model type and image dimensions, reducing complexity and potential user errors.

| **Dimension Option**             | **Model**             | **Resolution/Aspect Ratio** | **Description**                                                                                               |
| -------------------------------- | --------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `flux-dev: 1440x1440`            | `flux-dev`            | `1440x1440`                  | Standard square resolution for development model.                                                            |
| `flux-dev: 1440x896`             | `flux-dev`            | `1440x896`                   | Landscape resolution for development model.                                                                   |
| `flux-dev: 896x1440`             | `flux-dev`            | `896x1440`                   | Portrait resolution for development model.                                                                    |
| `flux-pro-1.1: 1440x1440`        | `flux-pro-1.1`        | `1440x1440`                  | Standard square resolution for pro-1.1 model.                                                                 |
| `flux-pro-1.1: 1440x896`         | `flux-pro-1.1`        | `1440x896`                   | Landscape resolution for pro-1.1 model.                                                                      |
| `flux-pro-1.1: 896x1440`         | `flux-pro-1.1`        | `896x1440`                   | Portrait resolution for pro-1.1 model.                                                                       |
| `flux-pro-1.1-ultra: 1:1`        | `flux-pro-1.1-ultra`  | `1:1`                        | Square aspect ratio for ultra model.                                                                          |
| `flux-pro-1.1-ultra: 16:9`       | `flux-pro-1.1-ultra`  | `16:9`                       | Widescreen aspect ratio for ultra model.                                                                       |
| `flux-pro-1.1-ultra: 9:16`       | `flux-pro-1.1-ultra`  | `9:16`                       | Portrait aspect ratio for ultra model.                                                                        |

This standardized approach ensures that users can effortlessly select the desired model and its corresponding settings without dealing with multiple, separate parameters.

#### Image Resolution

From the list presented, only the flux-pro-1.1-ultra model can generate high-resolution images. The following resolutions are acceptable for the other models:

| Model                   | Max Width (px) | Min Width (px) | Max Height (px) | Min Height (px) |
| ----------------------- | -------------- | -------------- | ---------------- | ---------------- |
| `flux-dev`              | 1440           | 256            | 1440             | 256              |
| `flux-pro-1.1`          | 1440           | 256            | 1440             | 256              |
| `flux-pro-1.1-ultra`    | 2752           | 256            | 2752             | 256              |

Go to the Open WebUI home page and in the list of models, look for `Black Forest Labs: FLUX 1.1 Pro`. Enter the prompt in the chat field with the model. If the parameters are correct, the generated image will appear in the chat box.

**Recommendation:** To view the image in its original uncompressed resolution, open it in a new tab of your browser.

### Saving images

Flux API returns the URL of the image stored in the Black Forest Labs cloud storage. The plugin accesses the Open WebUI environment variables and saves the image in the standard `~/open-webui/backend/data/cache/image/generations` directory. The downloaded image is rendered on the dialog page from local storage.

## Error Handling

- **RawValidationError**: Raised when the RAW option is used with unsupported models.
- **Timeout**: Raised if the result is not ready within the specified timeout period.  
- **API Errors**: Handles request and response errors from the FLUX API.

## Backlog

1. Add image generation parameters: sampling steps, guidance scale.
2. Make changes in system promt to follow the instruction exactly in case the user asks not to optimize his promt.

## License

This function is licensed under the MIT License.

## Contributing

Contributions are welcome! If you find a bug or have a feature request, feel free to open an issue or submit a pull request.
