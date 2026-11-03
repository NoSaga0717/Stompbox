#include "NAM.h"

static const std::vector<std::string> outputModeNames = { "Normalized", "Calibrated" };

// Reads a numeric metadata field ("input_level_dbu", ...). NAN if missing.
static float ReadModelLevel(NeuralAudio::NeuralModel* model, const char* field)
{
    std::string value = model->GetMetadata(field);

    if (value.empty())
        return NAN;

    try
    {
        return std::stof(value);
    }
    catch (const std::exception&)
    {
        return NAN;
    }
}

NeuralAudio::NeuralModel* NAMLoader::LoadFromFile(const std::filesystem::path& loadPath)
{
    // Models must be ready for the largest block the host will send
    modelLoader.SetDefaultMaxAudioBufferSize(std::max(StompBox::MaxAudioBufferSize, 32));

    auto model = modelLoader.CreateFromFile(loadPath);

    if (model != nullptr)
    {
        NAMModelLevels& slot = levelSlots[nextLevelSlot];

        slot.InputDBu = ReadModelLevel(model, "input_level_dbu");
        slot.OutputDBu = ReadModelLevel(model, "output_level_dbu");
        slot.Model = model;

        nextLevelSlot = (nextLevelSlot + 1) % NumLevelSlots;
    }

    return model;
}

NAMModelLevels NAMLoader::GetLevels(NeuralAudio::NeuralModel* model) const
{
    for (int i = 0; i < NumLevelSlots; i++)
    {
        if (levelSlots[i].Model == model)
            return levelSlots[i];
    }

    return NAMModelLevels();
}

NAM::NAM(const std::string folderName, const std::vector<std::string> fileExtensions, const std::filesystem::path& basePath) :
    fileType(folderName, fileExtensions, basePath),
    namLoader(fileType)
{
    Name = "NAM";
    Description = "Neural Amp Modeler capture playback";

    auto& modelParam = AddParameter();
    modelParam.Name = "Model";
    modelParam.SourceVariable = &modelIndex;
    modelParam.ParameterType = PARAMETER_TYPE_FILE;
    modelParam.FilePath = "NAM";
    modelParam.EnumValues = &fileType.GetFileNames();
    modelParam.DefaultValue = -1;
    modelParam.Description = "Selected NAM model";
    modelParam.MinValue = -1;
    modelParam.MaxValue = (float)(fileType.GetFileNames().size()) - 1;

    auto& qualityParam = AddParameter();
    qualityParam.Name = "Quality";
    qualityParam.SourceVariable = &qualityScale;
    qualityParam.ParameterType = PARAMETER_TYPE_KNOB;
    qualityParam.DefaultValue = 1.0f;
    qualityParam.Description = "Model quality scale factor";
    qualityParam.MinValue = 0.0f;
    qualityParam.MaxValue = 1.0f;
    qualityParam.IsAdvanced = true;

    auto& outputModeParam = AddParameter();
    outputModeParam.Name = "OutputMode";
    outputModeParam.SourceVariable = &outputMode;
    outputModeParam.ParameterType = PARAMETER_TYPE_ENUM;
    outputModeParam.EnumValues = &outputModeNames;
    outputModeParam.DefaultValue = NAM_OUTPUT_NORMALIZED;
    outputModeParam.Description = "Normalized: same loudness for all models. Calibrated: same level as the captured gear";
    outputModeParam.MinValue = 0;
    outputModeParam.MaxValue = (float)outputModeNames.size() - 1;
    outputModeParam.IsAdvanced = true;
}

void NAM::init(int samplingFreq)
{
    StompBox::init(samplingFreq);
}

void NAM::SetParameterValue(StompBoxParameter& parameter, float value)
{
    StompBox::SetParameterValue(parameter, value);

    if (parameter.Name == "Model")
    {
        namLoader.LoadIndex((int)modelIndex);
    }
}

bool NAM::RescanFiles()
{
    std::filesystem::path currentPath;

    int index = (int)modelIndex;

    if ((index >= 0) && (index < (int)fileType.GetFilePaths().size()))
        currentPath = fileType.GetFilePaths()[index];

    fileType.IndexFiles();

    // Keep pointing at the same file (its index may have moved), without reloading it
    int newIndex = -1;

    const auto& paths = fileType.GetFilePaths();

    for (size_t i = 0; i < paths.size(); i++)
    {
        if (paths[i] == currentPath)
        {
            newIndex = (int)i;

            break;
        }
    }

    StompBoxParameter* modelParam = GetParameter("Model");

    modelParam->MaxValue = (float)(paths.size()) - 1;

    if (newIndex != -1)
    {
        modelIndex = (float)newIndex;
        namLoader.SetLoadedIndex(newIndex);
    }

    return true;
}

void NAM::compute(int count, float* input, float* output)
{
    auto activeModel = namLoader.GetCurrentData();

    if (activeModel == nullptr)
    {
        memcpy(output, input, count * sizeof(float));

        return;
    }

    if (activeModel != levelsModel)
    {
        levelsModel = activeModel;
        modelLevels = namLoader.GetLevels(activeModel);
    }

    if (qualityScale != currentQualityScale)
    {
        activeModel->SetQualityScaleFactor(qualityScale);

        currentQualityScale = qualityScale;
    }

    // Input calibration: match the interface level (dBu at 0 dBFS) to the level the model was
    // captured at. Only when the model says it.
    if (!std::isnan(modelLevels.InputDBu))
    {
        float inputAdjustDB = StompBox::AudioInputLevelDBu - modelLevels.InputDBu;

        if (inputAdjustDB != 0)
        {
            float inputGain = (float)pow(10.0, inputAdjustDB / 20.0);

            for (int i = 0; i < count; i++)
            {
                output[i] = input[i] * inputGain;
            }

            input = output;
        }
    }

    activeModel->Process(input, output, count);

    float adjustDB = 0;

    if ((int)outputMode == NAM_OUTPUT_CALIBRATED)
    {
        // Same dBu at the output as the real gear, for the same interface
        if (!std::isnan(modelLevels.OutputDBu))
            adjustDB = modelLevels.OutputDBu - StompBox::AudioInputLevelDBu;
    }
    else
    {
        adjustDB = activeModel->GetRecommendedOutputDBAdjustment();
    }

    if (adjustDB != 0)
    {
        float outputGain = (float)pow(10.0, adjustDB / 20.0);

        for (int i = 0; i < count; i++)
        {
            output[i] *= outputGain;
        }
    }
}
